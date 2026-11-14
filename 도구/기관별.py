"""기관별로 '원 보고서 PDF 후보'를 찾는 함수.

각 함수는 (기사에서 뽑은 정보, 라이너 검색 결과)를 받아
Candidate 목록(앞일수록 유력)을 돌려준다. PDF 를 받지는 않는다.
"""
import random
import re
import subprocess
import tempfile
import threading
import time
import urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

USER_AGENT = "Mozilla/5.0 (research prototype)"
RETRY_WAITS_SEC = [5, 20, 60]
SITE_PARALLEL = 2    # 기관 누리집마다 동시 요청 수(보사연은 연속 요청에 429)
CONNECT_TIMEOUT_SEC = 15  # 연결 맺기까지(11/15: 30 → 15)
TOTAL_TIMEOUT_SEC = 45    # 한 번 받기 전체(넘으면 curl 이 연결을 끊음, 11/15: 90 → 45 — 느린 누리집이 한 건을 붙잡지 않게)
MAX_BYTES = 60 * 1024 * 1024
# 11/13 보사연 서버가 연결만 연 채 응답을 질질 끌어 25분씩 멈춤. 파이썬 urllib 은 소켓 대기 제한만 있어
# 막지 못했고, 스레드로 포기해도 멈춘 연결이 동시 요청 자리를 계속 차지해 뒤 요청이 줄줄이 막힘
# → curl --max-time 으로 받는다(시간이 되면 연결 자체를 끊어 자리가 반드시 풀림).
CURL_TIMEOUT_EXIT = 28
CURL_TOO_BIG_EXIT = 63
KIHASA_PAGES_BUDGET_SEC = 120  # 보사연 상세 페이지 따라가기 전체 시간
# 기관의 '보도 설명자료·해명' 게시물은 문제가 된 기사 문장을 그대로 인용한다 → 원 보고서 후보로 쓰면
# 기사를 반박하는 문서에서 기사 문장을 찾아 '일치'라고 답하게 된다(11/13 오보 사례 #4). 후보에서 뺀다.
REBUTTAL_TITLE = re.compile(r"설명자료|해명|보도\s*관련|기사\s*관련|언론\s*보도|[」｣]\s*\(|[」｣]\s*보도")


def drop_rebuttals(cands: list) -> list:
    """보도 설명자료·해명 게시물은 원 보고서가 아니므로 뒤로 미룬다(11/15: 빼지는 않음 — 기사가 부처 설명자료를
    그대로 옮긴 경우 그 설명자료가 원문이다, 무작위3 #39 '사실은 이렇습니다')."""
    return sorted(cands, key=lambda c: bool(REBUTTAL_TITLE.search(c.title)))


MAX_CANDIDATES = 8   # 기관별로 모을 후보 수(받기는 원문찾기.py 의 MAX_DOWNLOAD 개만)
# 라이너 검색 'site:' 연산자에 쓸 기관 도메인
DOMAINS = {"자본시장연구원": "kcmi.re.kr", "국가데이터처": "mods.go.kr", "한국보건사회연구원": "kihasa.re.kr"}

# 기사·모델이 쓰는 여러 이름 → 우리가 쓰는 기관 이름
ALIASES = {
    "자본시장연구원": "자본시장연구원", "KCMI": "자본시장연구원",
    "국가데이터처": "국가데이터처", "통계청": "국가데이터처",
    "한국보건사회연구원": "한국보건사회연구원", "보건사회연구원": "한국보건사회연구원",
    "보사연": "한국보건사회연구원", "KIHASA": "한국보건사회연구원",
}


@dataclass(frozen=True)
class Candidate:
    title: str
    page_url: str   # 사람이 열어 볼 상세 페이지
    pdf_url: str
    file_key: str   # 저장 파일 이름용 (예: kcmi_2315)


# 보사연은 동시 2개·연속 요청에도 HTTP 429 를 자주 내고, 5·20·60초 쉬어도 계속 막혀 맞는 PDF 를 못 받음
# (11/13 처음 보는 #18) → 한 번에 하나씩, 요청 사이 최소 간격을 둔다.
SITE_PARALLEL_BY_HOST = {"www.kihasa.re.kr": 1}
SITE_MIN_GAP_SEC = {"www.kihasa.re.kr": 1.0}

_site_slots: dict[str, threading.Semaphore] = {}
_site_last: dict[str, float] = defaultdict(float)
_slots_lock = threading.Lock()


def _host(url: str) -> str:
    return urllib.parse.urlparse(url).netloc


def _slot(url: str) -> threading.Semaphore:
    host = _host(url)
    with _slots_lock:
        if host not in _site_slots:
            _site_slots[host] = threading.Semaphore(SITE_PARALLEL_BY_HOST.get(host, SITE_PARALLEL))
        return _site_slots[host]


def _wait_gap(url: str) -> None:
    """같은 누리집에 요청 사이 최소 간격 지키기(자리를 잡은 뒤 부름)."""
    host = _host(url)
    gap = SITE_MIN_GAP_SEC.get(host, 0)
    if gap:
        wait = _site_last[host] + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _site_last[host] = time.time()


def _curl_once(url: str, data: bytes | None) -> tuple[int, bytes]:
    """curl 로 한 번 받기 → (HTTP 상태, 내용). 시간 초과·연결 실패는 RuntimeError."""
    with tempfile.TemporaryDirectory() as tmp:
        out, jar = Path(tmp) / "body", Path(tmp) / "cookies"
        # 쿠키를 받아야 내려받기가 되는 누리집(법무부·경찰청: 쿠키 없이 같은 주소로 50번 넘게 되돌림) → 한 번 받는 동안 쿠키 유지
        cmd = ["curl", "-sS", "-L", "-b", str(jar), "-c", str(jar), "-A", USER_AGENT, "--connect-timeout", str(CONNECT_TIMEOUT_SEC),
               "--max-time", str(TOTAL_TIMEOUT_SEC), "--max-filesize", str(MAX_BYTES),
               "-o", str(out), "-w", "%{http_code}"]
        if data is not None:
            cmd += ["--data-binary", "@-"]  # urllib 과 같은 form 형식 POST
        proc = subprocess.run([*cmd, url], input=data, capture_output=True, timeout=TOTAL_TIMEOUT_SEC + 15)
        if proc.returncode == CURL_TIMEOUT_EXIT:
            raise RuntimeError(f"기관 누리집 받기가 {TOTAL_TIMEOUT_SEC}초 안에 끝나지 않아 끊음: {url}")
        if proc.returncode == CURL_TOO_BIG_EXIT:
            raise RuntimeError(f"받을 파일이 너무 큼({MAX_BYTES // 1024 // 1024}MB 넘음): {url}")
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(f"기관 누리집 연결 실패: {url} — {err}")
        status = int(proc.stdout.decode().strip() or 0)
        return status, out.read_bytes() if out.exists() else b""


def fetch_bytes(url: str, data: bytes | None = None) -> bytes:
    """기관 누리집에서 받기(data 를 주면 POST). 누리집마다 동시 SITE_PARALLEL 개까지.
    '요청이 너무 많음'(429·503)이면 5·20·60초 쉬고 다시 시도."""
    for wait in [*RETRY_WAITS_SEC, None]:
        with _slot(url):
            _wait_gap(url)
            status, body = _curl_once(url, data)
        if 200 <= status < 300:
            return body
        if status not in (429, 503) or wait is None:
            raise RuntimeError(f"기관 누리집 받기 실패(HTTP {status}): {url}")
        print(f"    (누리집이 요청을 잠시 막음 HTTP {status}, {wait}초 뒤 다시 시도)")
        time.sleep(wait + random.uniform(0, 2))
    raise AssertionError("도달하지 않음")


def fetch_many(urls: list[str]) -> dict[str, str | RuntimeError]:
    """여러 페이지를 동시에 받기(누리집마다 동시 수 제한은 fetch_bytes 가 지킴). 실패는 RuntimeError 로 담음."""
    def one(u: str):
        try:
            return fetch_text(u)
        except RuntimeError as e:
            return e
    with ThreadPoolExecutor(max(1, min(6, len(urls)))) as ex:
        return dict(zip(urls, ex.map(one, urls)))


def fetch_text(url: str) -> str:
    return fetch_bytes(url).decode("utf-8", "replace")


ALIAS_PREFIX = re.compile(r"^(?:대한민국|한국|우리나라|국가|정부)?$")


def normalize_institution(name: str) -> str | None:
    """세 기관 전용 경로 이름으로. 이름 전체가 같거나 앞에 '대한민국·한국'만 붙은 경우만
    (11/15 검증 세트: '러시아 통계청'을 국가데이터처로 바꿔 우리 인구동향에 없다며 '원문에 없음' 헛답)."""
    for part in re.split(r"[·・,/]|및|와|과(?=[가-힣]{2,})", re.sub(r"\s|\(.*?\)", "", name or "")):   # 공동 발표 이름
        for alias, official in ALIASES.items():
            if part.endswith(alias) and ALIAS_PREFIX.match(part[: -len(alias)]):
                return official
    return None


# ── 자본시장연구원: 라이너 검색에서 보고서 상세 페이지 → PDF 링크 ─────────────
KCMI_DETAIL = re.compile(r"^https?://(?:www\.)?kcmi\.re\.kr/report/report_view\?report_no=(\d+)")
KCMI_PDF = re.compile(r"/common/downloadw\?fid=(\d+)&fgu=\d+&fty=\d+")


def kcmi(info: dict, results: list[dict]) -> list[Candidate]:
    """라이너 검색 결과의 보고서 상세 페이지 → PDF 링크, 또는 PDF 직접 링크(filetype:pdf 검색)."""
    out, seen, details = [], set(), []
    for r in results:
        url = r.get("url", "")
        direct = KCMI_PDF.search(url) if "kcmi.re.kr" in url else None
        if direct and direct.group(1) not in seen:
            seen.add(direct.group(1))
            out.append(Candidate(r.get("title", ""), url, url, f"kcmi_f{direct.group(1)}"))
            continue
        if KCMI_DETAIL.match(url):
            details.append(r)
    pages = fetch_many([r["url"] for r in details])   # 상세 페이지는 동시에 받는다
    for r in details:
        url = r["url"]
        m = KCMI_DETAIL.match(url)
        html = pages[url]
        if isinstance(html, RuntimeError):
            print(f"    {html}")
            continue
        link = KCMI_PDF.search(html)
        if link and link.group(1) not in seen:
            seen.add(link.group(1))
            out.append(Candidate(r.get("title", ""), url,
                                 "https://www.kcmi.re.kr" + link.group(0), f"kcmi_{m.group(1)}"))
    return out[:MAX_CANDIDATES]


# ── 보건사회연구원: 발간자료 상세(우선) 또는 보도자료 상세 → 첫 PDF 링크 ─────────
KIHASA_DETAIL = re.compile(r"^https?://www\.kihasa\.re\.kr/(publish/[\w/]+|news/press)/view\?(?:[^#\s]*&)?seq=(\d+)")
KIHASA_PDF = re.compile(r"/api/kihasa/file/download\?seq=(\d+)")


KIHASA_REPORT_LINK = re.compile(r"/publish/[a-z/]+/view\?seq=\d+")
KIHASA_TITLE = re.compile(r"<title>([^<|]+)")


def kihasa(info: dict, results: list[dict]) -> list[Candidate]:
    """보도자료 페이지만 잡히면, 그 안의 원 보고서(발간자료) 링크를 따라가 원 보고서를 먼저 쓴다."""
    pages: list[tuple[str, str]] = []  # (주소, 제목)
    out, seen = [], set()
    for r in results:
        url = r.get("url", "")
        direct = KIHASA_PDF.search(url) if "kihasa.re.kr" in url else None
        if direct and f"kihasa_{direct.group(1)}" not in seen:  # PDF 직접 링크(filetype:pdf 검색)
            seen.add(f"kihasa_{direct.group(1)}")
            out.append(Candidate(r.get("title", ""), url, url, f"kihasa_{direct.group(1)}"))
            continue
        if KIHASA_DETAIL.match(url):
            pages.append((url, r.get("title", "")))
    pages.sort(key=lambda p: 0 if "/publish/" in p[0] else 1)  # 원 보고서 먼저
    queue = list(pages)
    deadline = time.time() + KIHASA_PAGES_BUDGET_SEC
    deferred: set[str] = set()  # 한 번 뒤로 미룬 보도자료
    while queue and len(out) < MAX_CANDIDATES:
        if time.time() > deadline:  # 누리집이 느리거나 429 를 낼 때 한 건이 끝없이 길어지지 않게
            print(f"    (보사연 상세 페이지 찾기 {KIHASA_PAGES_BUDGET_SEC}초 넘어 남은 {len(queue)}곳은 건너뜀)")
            break
        url, title = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            html = fetch_text(url)
        except RuntimeError as e:  # 페이지 하나 실패는 건너뜀(전에는 이 건 전체가 오류로 끝남)
            print(f"    보사연 페이지 건너뜀: {e}")
            continue
        if "/news/press/" in url and url not in deferred:
            reports = ["https://www.kihasa.re.kr" + rel for rel in KIHASA_REPORT_LINK.findall(html)]
            reports = [r for r in reports if r not in seen]
            if reports:  # 원 보고서를 보도자료보다 앞에, 보도자료는 맨 뒤 예비 후보로(한 번만 미룸)
                queue[:0] = [(r, "") for r in reports]
                queue.append((url, title))
                deferred.add(url)
                seen.discard(url)
                continue
            # 11/13: 원 보고서 링크 없는 보도자료 둘이 서로를 뒤로 미루며 끝없이 다시 받던 버그 수정
        if not title:
            t = KIHASA_TITLE.search(html)
            title = t.group(1).strip() if t else url
        # 첨부가 여러 개(본문·요약·별책)일 수 있어 모두 후보로(첫 링크만 쓰던 것이 기존 #21 원인 추정)
        for n, link in enumerate(KIHASA_PDF.finditer(html)):
            if f"kihasa_{link.group(1)}" in seen:
                continue
            seen.add(f"kihasa_{link.group(1)}")
            out.append(Candidate(title + (f" (첨부 {n + 1})" if n else ""), url,
                                 "https://www.kihasa.re.kr" + link.group(0), f"kihasa_{link.group(1)}"))
    return out[:MAX_CANDIDATES]


# ── 국가데이터처: 라이너 검색에 원 보도자료가 안 잡힘 → '보도자료 전체' 게시판 제목 검색 ──
MODS_BOARD = "https://mods.go.kr/board.es"
MODS_REF_BID = ("203,204,205,206,207,210,211,11109,11113,11814,213,215,214,11860,11695,216,218,"
                "219,220,10820,11815,11895,11816,208,245,222,223,225,226,227,228,229,230,11321,"
                "232,233,234,12029,10920,11469,11470,11817,236,237,11471,238,240,241,11865,243,"
                "244,11893,11898,12031,11825,246,0067,0073,0079,0084")
MODS_ITEM = re.compile(  # 속성 사이 줄바꿈·공백이 제각각이라 \s+ 로 받는다
    r'href="(/boardDownload\.es\?bid=(\d+)&list_no=(\d+)&seq=\d+)"\s+class="bf_pdf">\s*'
    r'<span class="hdn">\s*(.*?)의 pdf파일')
PERIOD_TOKEN = re.compile(r"\d{4}년|\d{1,2}월|\d분기|[상하]반기")


# 보고서 이름이 없을 때 핵심어로 정기통계 이름을 짐작(게시판 제목 검색용)
SURVEY_BY_WORD = [("취업자|실업|고용률|고용", "고용동향"), ("소비자물가|물가", "소비자물가동향"),
                  ("산업생산|소매판매|설비투자|산업활동", "산업활동동향"), ("출생|사망|혼인|이혼", "인구동향"),
                  ("가계|소득|지출|적자가구", "가계동향조사"), ("수출|수입", "수출입"),
                  ("인구이동|전입|전출", "국내인구이동통계"), ("온라인쇼핑", "온라인쇼핑동향")]


def _period_prefixes(period: str) -> list[str]:
    """'2018년 3월' → ['2018년 3월'], '2025년 4분기' → ['2025년 4분기', '2025년 4/4분기'], '2024년' → ['2024년']"""
    year = re.search(r"(20\d{2})\s*년", period)
    if not year:
        return []
    y = year.group(1)
    if m := re.search(r"(\d{1,2})\s*월", period):
        return [f"{y}년 {int(m.group(1))}월"]
    if q := re.search(r"(\d)\s*(?:/4)?\s*분기", period):
        return [f"{y}년 {q.group(1)}분기", f"{y}년 {q.group(1)}/4분기"]
    return [f"{y}년"]


def _mods_keyword(info: dict) -> str:
    """'2026년 8월 경제활동인구조사 근로형태별 부가조사 결과' → '경제활동인구조사 근로형태별 부가조사'
    보고서 이름이 없으면 핵심어로 정기통계 이름을 짐작한다(예: 취업자 → 고용동향)."""
    title = info.get("보고서") or ""
    title = re.sub(r"\d{4}년|\d{1,2}월|\d분기|[상하]반기|결과|보도자료|[『』「」'\"]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    if title:
        return title
    words = " ".join(info.get("핵심어", []))
    return next((name for pat, name in SURVEY_BY_WORD if re.search(pat, words)), "")


MODS_VIEW = re.compile(r"^https?://(?:www\.)?mods\.go\.kr/board\.es\?.*?bid=(\d+).*?list_no=(\d+)")


def _mods_from_view(url: str, title: str) -> Candidate | None:
    """라이너 검색이 준 보도자료 상세(act=view) 페이지에서 PDF 첨부를 찾는다."""
    if "act=view" not in url:
        return None
    m = MODS_VIEW.match(url.replace("%26", "&"))
    if not m:
        return None
    html = fetch_text(url.replace("%26", "&"))
    pdf = re.search(rf'href="(/boardDownload\.es\?bid={m.group(1)}&(?:amp;)?list_no={m.group(2)}'
                    rf'&(?:amp;)?seq=\d+)"[^>]*>[^<]*\.pdf', html)
    if not pdf:
        return None
    return Candidate(title, url, "https://mods.go.kr" + pdf.group(1).replace("&amp;", "&"),
                     f"mods_{m.group(2)}")


def mods(info: dict, results: list[dict]) -> list[Candidate]:
    """① 라이너 site: 검색이 준 보도자료 상세 페이지 ② 누리집 '보도자료 전체' 게시판 제목 검색."""
    out, seen = [], set()
    for r in results:
        try:
            c = _mods_from_view(r.get("url", ""), r.get("title", ""))
        except RuntimeError as e:  # 페이지 하나 실패는 건너뜀
            print(f"    {e}")
            c = None
        if c and c.file_key not in seen:
            seen.add(c.file_key)
            out.append(c)
    keyword = _mods_keyword(info)
    # 게시판 제목 검색은 최근 글부터 한 쪽만 준다 → 기준 시점을 넣은 검색어를 먼저(옛 판을 찾게).
    # 11/13 오보 사례: '소비자물가동향'만으로 찾아 2026년 판만 나와 2018년 3월 판을 못 찾음
    keywords = [f"{p} {keyword}" for p in _period_prefixes(info.get("기준시점") or "")] + [keyword] if keyword else []
    for kw in keywords:
        query = urllib.parse.urlencode({"mid": "a10301010000", "bid": "a103010100",
                                        "ref_bid": MODS_REF_BID, "keyField": "T", "keyWord": kw})
        try:
            board = fetch_text(f"{MODS_BOARD}?{query}")
        except RuntimeError as e:  # 검색어 하나 실패는 나머지로
            print(f"    데이터처 게시판 검색 실패 '{kw}': {e}")
            continue
        for m in MODS_ITEM.finditer(board):
            path, bid, list_no, title = m.groups()
            if f"mods_{list_no}" in seen:
                continue
            seen.add(f"mods_{list_no}")
            page = f"{MODS_BOARD}?mid=a10301010000&bid={bid}&act=view&list_no={list_no}"
            out.append(Candidate(title.strip(), page, "https://mods.go.kr" + path, f"mods_{list_no}"))
    # 기간(2026년, 7월, 4분기)이 제목에 든 글을 앞으로. 최종 확인은 PDF 앞쪽으로(원문찾기.py)
    tokens = PERIOD_TOKEN.findall((info.get("기준시점") or "") + " " + (info.get("보고서") or ""))
    if tokens:
        out.sort(key=lambda c: -sum(t in c.title for t in tokens))
    return out[:MAX_CANDIDATES]


FINDERS = {"자본시장연구원": kcmi, "한국보건사회연구원": kihasa, "국가데이터처": mods}
