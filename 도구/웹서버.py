"""「원래 어디서?」 웹 화면 서버 (표준 라이브러리 + PyMuPDF).

실행: python3 도구/웹서버.py   → 브라우저에서 http://127.0.0.1:8765 (자리가 차 있으면 ORIGIN_PORT=8766)
- POST /api/check   {"sentence", "context", "date", "force", "prefer"} → {"job": id}
  prefer: '다른 후보'에서 사람이 고른 보고서 {"제목","상세페이지","PDF주소","키"} (지원 기관 PDF 주소만)
  force: 입구 필터가 '통계 문장 아님'이라 해도 확인(화면의 '그래도 확인하기')
- GET  /api/job?id= → {"status": running|done|error, "steps": [...], "events": [...], "partial": {...},
                       "result": {...}, "error": "..."}
  events: 단계별 결과(분해·후보·보고서·쪽·판정)를 나오는 대로. partial: 교차 확인 전 임시 판정(먼저 화면에)
- GET  /api/page?file=&page=&mark=&quote=  → 원문 쪽 PNG (숫자·문장에 형광펜 표시, 그 부분만 잘라서)
- GET  /api/pdf?file=  → 원문 PDF
한 번에 한 건씩 처리한다(진행 기록을 화면에 보여 주려고 출력을 가로채기 때문).
"""
import contextlib
import io
import json
import os
import re
import sys
import threading
import traceback
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PDF도구 import PDF_DIR
from 기사받기 import fetch_article, valid_url
from 격리실행 import run_isolated
from 원문찾기 import save

HOST, PORT = "127.0.0.1", int(os.environ.get("ORIGIN_PORT", "8765"))   # 자리가 차 있으면 ORIGIN_PORT=8766
WEB_DIR = Path(__file__).resolve().parent / "웹"
SAFE_PDF = re.compile(r"^[\w가-힣.\-]+\.pdf$")
ZOOM = 2.0                 # 쪽 그림 해상도 배율
CROP_MARGIN = 170          # 형광펜 위아래로 보여 줄 여백(pt)
MARK = (1.0, 0.894, 0.361)  # 형광 노랑 #FFE45C

JOBS: dict[str, dict] = {}
RUN_LOCK = threading.Lock()


class StepWriter(io.TextIOBase):
    """run() 이 찍는 진행 기록을 한 줄씩 작업 상태에 쌓는다."""

    def __init__(self, job: dict):
        self.job, self.buf = job, ""

    def write(self, s: str) -> int:
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                self.job["steps"].append(line.strip())
        return len(s)


# '다른 후보로 다시 확인'에서 받는 PDF 주소는 지원 기관 누리집만(임의 주소를 서버가 받으러 가지 않게)
PREFER_HOSTS = re.compile(r"^https://[A-Za-z0-9.-]+\.(?:go|re|or)\.kr/")   # 공공·연구기관 누리집(세 기관 포함)


def valid_prefer(raw) -> dict | None:
    """화면이 보낸 '사람이 고른 보고서'를 검사한다. 이상하면 None."""
    if not isinstance(raw, dict):
        return None
    pick = {k: str(raw.get(k) or "")[:500] for k in ("제목", "상세페이지", "PDF주소", "키")}
    if not PREFER_HOSTS.match(pick["PDF주소"]) or not re.fullmatch(r"[A-Za-z0-9_]{3,60}", pick["키"]):
        return None
    return pick


def work(job_id: str, sentence: str, context: str, date: str, force: bool, prefer: dict | None = None,
         url: str = "") -> None:
    job = JOBS[job_id]

    def on_event(kind: str, data) -> None:
        job["events"].append({"종류": kind, "내용": data})
        if kind in ("판정", "중간답"):   # 중간답: 1차에서 못 찾은 결과를 먼저(Deep Research 로 더 찾는 동안)
            job["partial"] = data
            job["partial_n"] = job.get("partial_n", 0) + 1

    with RUN_LOCK, contextlib.redirect_stdout(StepWriter(job)):
        try:
            if url and not (context and date):  # 기사 링크만 넣었으면 문맥·날짜를 기사에서 채운다(A5)
                try:
                    art = fetch_article(url, sentence)
                    context, date = context or art["문맥"], date or art["날짜"]
                    print(f"[0] 기사 주소에서 제목·날짜·앞뒤 문단을 읽음: {art['제목'][:40]} ({art['날짜'] or '날짜 모름'})")
                except RuntimeError as e:  # 기사 받기 실패는 문장만으로 계속(이유는 화면에)
                    print(f"[0] 기사 주소를 읽지 못해 문장만으로 찾습니다: {e}")
            # 별도 프로그램으로 돌려 시간 상한을 반드시 지킨다(11/16: 같은 프로그램 안의 상한이 깨진 적 있음)
            result = run_isolated(sentence, context, date, force=force, prefer=prefer, on_event=on_event,
                                  on_line=lambda line: job["steps"].append(line.strip()))
            save(result)
            job.update(status="done", result=result)
        except Exception as e:  # 화면에 이유를 보여 주고, 서버 창에는 자세한 기록
            job.update(status="error", error=f"{type(e).__name__}: {e}")
            traceback.print_exc(file=sys.__stderr__)


def page_png(file: str, page_no: int, mark: str, quote: str, crop: bool) -> bytes:
    """원문 쪽을 그림으로. 숫자(mark)와 인용 문장 첫머리(quote)에 형광펜, crop 이면 그 부분만."""
    import fitz  # PyMuPDF
    doc = fitz.open(PDF_DIR / file)
    page = doc[page_no - 1]
    hits = []
    for one in [m for m in mark.split("|") if m.strip()]:  # 여러 숫자는 | 로 나눠 받음(재료 숫자 등)
        for text in {one, one.replace(" ", "")}:
            hits += page.search_for(text)
    if quote:
        head = re.sub(r"\s+", " ", quote).strip()[:18]
        hits += page.search_for(head) if len(head) >= 6 else []
    for r in hits:
        page.draw_rect(r + (-1.5, -1.5, 1.5, 1.5), color=None, fill=MARK, fill_opacity=0.55, overlay=False)
    clip = None
    if crop and hits:
        top = max(0, min(r.y0 for r in hits) - CROP_MARGIN)
        bottom = min(page.rect.height, max(r.y1 for r in hits) + CROP_MARGIN)
        clip = fitz.Rect(0, top, page.rect.width, bottom)
    png = page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM), clip=clip).tobytes("png")
    doc.close()
    return png


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 요청마다 찍히는 기본 기록은 끈다
        pass

    def send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj: dict, code: int = 200) -> None:
        self.send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        url = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        if url.path in ("/", "/index.html"):
            return self.static("index.html", "text/html; charset=utf-8")
        if url.path in ("/style.css", "/app.js"):
            ctype = "text/css" if url.path.endswith(".css") else "text/javascript"
            return self.static(url.path[1:], ctype + "; charset=utf-8")
        if url.path == "/api/job":
            job = JOBS.get(q.get("id", ""))
            return self.send_json(job) if job else self.send_json({"error": "없는 작업 번호입니다."}, 404)
        if url.path in ("/api/page", "/api/pdf"):
            file = q.get("file", "")
            if not SAFE_PDF.match(file) or not (PDF_DIR / file).exists():
                return self.send_json({"error": "원문 PDF를 찾을 수 없습니다."}, 404)
            if url.path == "/api/pdf":
                return self.send(200, (PDF_DIR / file).read_bytes(), "application/pdf")
            try:
                png = page_png(file, int(q.get("page", "1")), q.get("mark", ""), q.get("quote", ""),
                               q.get("crop") == "1")
            except (ValueError, IndexError) as e:
                return self.send_json({"error": f"쪽 그림을 만들지 못했습니다: {e}"}, 400)
            return self.send(200, png, "image/png")
        self.send_json({"error": "없는 주소입니다."}, 404)

    def do_POST(self) -> None:
        if self.path != "/api/check":
            return self.send_json({"error": "없는 주소입니다."}, 404)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except json.JSONDecodeError:
            return self.send_json({"error": "요청 형식이 JSON이 아닙니다."}, 400)
        sentence = (body.get("sentence") or "").strip()
        if not (8 <= len(sentence) <= 1000) or not re.search(r"\d", sentence):
            return self.send_json({"error": "숫자가 들어 있는 기사 문장을 8~1000자로 넣어 주세요."}, 400)
        url = (body.get("url") or "").strip()[:1000]
        if url and not valid_url(url):
            return self.send_json({"error": "기사 주소는 http:// 또는 https:// 로 시작해야 합니다."}, 400)
        prefer = valid_prefer(body.get("prefer")) if body.get("prefer") else None
        if body.get("prefer") and prefer is None:
            return self.send_json({"error": "고른 보고서 정보가 올바르지 않습니다(지원 기관 누리집 PDF만 됩니다)."}, 400)
        job_id = uuid.uuid4().hex[:12]
        JOBS[job_id] = {"status": "running", "steps": [], "events": [], "partial": None, "result": None, "error": None}
        threading.Thread(target=work, daemon=True, args=(
            job_id, sentence, (body.get("context") or "").strip()[:3000], (body.get("date") or "").strip()[:20],
            body.get("force") is True, prefer, url,
        )).start()
        self.send_json({"job": job_id})

    def static(self, name: str, ctype: str) -> None:
        path = WEB_DIR / name
        if not path.exists():
            return self.send_json({"error": f"화면 파일이 없습니다: {name}"}, 404)
        self.send(200, path.read_bytes(), ctype)


if __name__ == "__main__":
    print(f"「원래 어디서?」 화면: http://{HOST}:{PORT}  (끄려면 Ctrl+C)")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
