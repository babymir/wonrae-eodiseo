"""국가데이터처가 실제 기사에 낸 '보도 설명자료'(KOSIS 통계 Fact-Check 목록)를 모은다.

왜: 지금 평가 세트에는 '실제로 틀린 기사'가 거의 없다. 기관이 직접 해명한 기사는 정답(무엇이 틀렸나)이
    설명자료에 적혀 있어, 도구가 그 문제를 짚는지 독립적으로 시험할 수 있다(우리 개발에 쓰지 않은 자료).
출처: https://kosis.kr/serviceInfo/factCheckList.do (목록 116건, 2026-11-13 확인)

사용: python3 도구/오보사례_모으기.py
결과: 작업/평가_오보사례/목록.json  (순번·보도일자·매체·제목·관련보도 주소·설명자료 PDF·설명 글)
      작업/평가_오보사례/설명자료/<번호>.pdf  (중간 저장: 받은 것은 다시 받지 않음)
"""
import html
import json
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from 기관별 import fetch_bytes  # noqa: E402  (curl·시간 제한·누리집별 간격을 그대로 씀)

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "작업" / "평가_오보사례"
PDF_DIR = OUT_DIR / "설명자료"
LIST_URL = "https://kosis.kr/serviceInfo/factCheckList.do"
FILE_URL = "https://kosis.kr/cmm/fms/FactCheckFileDown.do?"
MAX_PAGES = 30          # 목록 쪽 수 상한(쪽당 10건)
TEXT_CHARS = 3000       # 설명 글 저장 길이
MIN_TEXT = 200          # 이보다 짧으면 글자 추출 실패로 보고 다른 형식으로

ROW = re.compile(r"<tr>\s*<td>(\d+)</td>\s*<td>([\d-]+)</td>\s*<td>([^<]*)</td>(.*?)</tr>", re.S)
TITLE = re.compile(r'class="resultListObj">(.*?)</a>', re.S)
LINK = re.compile(r"fn_linkUp\('([^']+)'\)")
FILE = re.compile(r"fn_egov_FactdownFile\('([^']+)',\s*'(\d+)',\s*'([^']+\.(pdf|hwpx|hwp))',\s*'([^']+)'\)")
FORMAT_ORDER = ["pdf", "hwpx", "hwp"]   # 글자 뽑기 쉬운 순서(같은 설명자료가 여러 형식으로 올라옴)
# hwp5txt 는 표 안 글자를 건너뜀(설명자료는 대부분 표 안에 씀) → hwp5proc xml 에서 <Text> 를 모은다
HWP5PROC = Path.home() / "Library/Python/3.9/bin/hwp5proc"


def list_page(no: int) -> list[dict]:
    raw = fetch_bytes(LIST_URL, f"pageIndex={no}".encode()).decode("utf-8", "replace")
    rows = []
    for m in ROW.finditer(raw):
        rest = m.group(4)
        title = TITLE.search(rest)
        link = LINK.search(rest)
        files = sorted(FILE.findall(rest), key=lambda f: FORMAT_ORDER.index(f[3]))
        rows.append({"순번": int(m.group(1)), "보도일자": m.group(2), "매체": m.group(3).strip(),
                     "제목": html.unescape(re.sub(r"\s+", " ", title.group(1))).strip() if title else "",
                     "관련보도": link.group(1) if link else "",
                     # 같은 설명자료가 여러 형식으로 올라옴. 서버에 없는 형식(HTTP 500)이 있어 차례로 시도한다
                     "설명자료": [{"store": f[0], "id": f[1], "name": f[2], "ext": f[3], "type": f[4]} for f in files]})
    return rows


def _hwpx_text(path: Path) -> str:
    """HWPX(압축 파일 안 XML)에서 글자만."""
    import zipfile
    with zipfile.ZipFile(path) as z:
        parts = [z.read(n).decode("utf-8", "replace") for n in sorted(z.namelist())
                 if re.match(r"Contents/section\d+\.xml", n)]
    return html.unescape(re.sub(r"<[^>]+>", " ", " ".join(parts)))


def _download(f: dict, path: Path) -> None:
    url = FILE_URL + urllib.parse.urlencode({"atchFileId": f["id"], "sotreType": f["store"],
                                             "orignlFileNm": f["name"], "fileType": f["type"]})
    data = fetch_bytes(url)
    if len(data) < 1000:
        raise RuntimeError(f"파일이 아닌 응답({len(data)}바이트)")
    path.write_bytes(data)


def _extract(path: Path, ext: str) -> str:
    if ext == "pdf":
        return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, timeout=60).stdout
    if ext == "hwpx":
        return _hwpx_text(path)
    xml = subprocess.run([str(HWP5PROC), "xml", str(path)], capture_output=True, text=True, timeout=120).stdout
    return html.unescape(" ".join(re.findall(r"<Text[^>]*>([^<]*)</Text>", xml)))


def pdf_text(row: dict) -> str:
    """설명자료(PDF·HWPX·HWP)를 형식 순서대로 받아 글자를 뽑는다. 받은 파일은 다시 받지 않음.
    글자가 MIN_TEXT 보다 짧으면(추출 실패) 다음 형식으로."""
    errors = []
    for f in row["설명자료"]:
        path = PDF_DIR / f"{row['순번']}.{f['ext']}"
        try:
            if not path.exists():
                _download(f, path)
            text = re.sub(r"[ \t]+", " ", _extract(path, f["ext"])).strip()
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
            errors.append(f"{f['ext']}: {e}"[:120])
            continue
        if len(text) >= MIN_TEXT:
            return text[:TEXT_CHARS]
        errors.append(f"{f['ext']}: 글자 {len(text)}자뿐")
    raise RuntimeError(" / ".join(errors) or "설명자료 파일 없음")


def main() -> None:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "목록.json"
    done = {r["순번"]: r for r in json.loads(out_path.read_text(encoding="utf-8"))} if out_path.exists() else {}
    rows: list[dict] = []
    for no in range(1, MAX_PAGES + 1):
        page = list_page(no)
        if not page:
            break
        rows += page
    print(f"목록 {len(rows)}건")
    for r in rows:
        if r["순번"] in done and done[r["순번"]].get("설명"):
            r["설명"] = done[r["순번"]]["설명"]
            continue
        try:
            r["설명"] = pdf_text(r)
        except (RuntimeError, subprocess.TimeoutExpired) as e:  # 한 건 실패는 기록하고 계속
            r["설명"], r["오류"] = "", f"설명자료 받기 실패: {e}"
            print(f"  {r['순번']}번 {r['오류']}")
        done[r["순번"]] = r
        out_path.write_text(json.dumps(sorted(done.values(), key=lambda x: -x["순번"]), ensure_ascii=False, indent=1),
                            encoding="utf-8")  # 한 건마다 저장(다시 실행하면 이어서)
    ok = sum(1 for r in done.values() if r.get("설명"))
    print(f"설명 글 {ok}/{len(done)}건 → {out_path}")


if __name__ == "__main__":
    main()
