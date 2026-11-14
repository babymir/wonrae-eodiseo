"""받은 파일(PDF·HWP·HWPX)의 글자와 게시물 페이지의 첨부파일 링크(11/14).

국내 기관 원문은 대개 게시물에 첨부된 HWP·HWPX·PDF 다. 게시물 본문 글자만 읽어서는 숫자를 못 찾는다
(무작위 60건: 원문이 공개돼 있는데 못 읽은 22건 중 약 8건이 국내 보도자료·첨부).
- HWPX: zip 안 Contents/section*.xml 의 글자(문단마다 줄바꿈)
- HWP(5.0): pyhwp 의 hwp5txt 명령(설치: pip install pyhwp). 없으면 이유를 출력하고 건너뜀
- PDF: pdftotext
"""
import io
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import zipfile
from pathlib import Path

EXTRACT_TIMEOUT_SEC = 120
MAX_ATTACHMENTS = 3
HWP5_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")   # OLE 복합 문서(HWP 5.0)
HWP5TXT = shutil.which("hwp5txt") or str(Path.home() / "Library/Python/3.9/bin/hwp5txt")
# 첨부파일 링크로 보이는 주소(게시판마다 이름이 달라 흔한 것을 모음). 그림·누리집 공통 파일은 뺀다
ATTACH_HREF = re.compile(r"download|filedown|atchfile|getfile|fileview|\.pdf\b|\.hwpx?\b", re.I)
NOT_ATTACH = re.compile(r"\.(?:jpe?g|png|gif|svg|css|js|zip|mp4)\b|/images?/|/img/|isms|iso27|privacy|개인정보", re.I)
# 원문이 아닌 안내 문서(방법론·이용 안내 등)는 첨부로 읽지 않는다(11/14: IEA 'methodology PDF')
NOT_SOURCE_NAME = re.compile(r"methodolog|guide|manual|terms|privacy|방법론|가이드|매뉴얼|이용약관|개인정보|서식|양식|신청서", re.I)
ANCHOR = re.compile(r"<a\b[^>]*?href=(?:\"([^\"]+)\"|'([^']+)')[^>]*>(.*?)</a>", re.I | re.S)   # href 안 다른 따옴표 허용
# 자바스크립트로 내려받는 첨부: data-url="…hwpx" data-name="…" / downloadFile('…hwpx', '이름') (11/14 경기도 보도자료)
SCRIPT_FILE = re.compile(r"data-url=[\"']([^\"']+\.(?:hwpx?|pdf|xlsx))[\"'][^>]*?(?:data-name=[\"']([^\"']*)[\"'])?"
                         r"|\w*[Dd]own\w*\(\s*'([^']+\.(?:hwpx?|pdf|xlsx))'\s*(?:,\s*'([^']*)')?", re.I)


def kind_of(data: bytes) -> str:
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"PK"):
        try:   # 그림이 앞에 든 HWPX 는 앞부분에 본문 표시가 없다 → zip 목록으로 판별
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            return "기타"
        if any(n.startswith("Contents/section") for n in names):
            return "hwpx"
        return "xlsx" if any(n.startswith("xl/worksheets/") for n in names) else "기타"
    if data.startswith(HWP5_MAGIC):
        return "hwp"
    return "html" if b"<" in data[:2000] else "기타"


def hwpx_text(data: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(data))
    parts = []
    for name in sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n)):
        xml = z.read(name).decode("utf-8", "replace")
        xml = re.sub(r"</hp:p>|</hp:tc>", "\n", xml)          # 문단·표 칸 끝에서 줄바꿈
        parts.append(re.sub(r"<[^>]+>", "", xml))
    return re.sub(r"\n\s*\n+", "\n", "\n".join(parts))


def hwp_text(data: bytes) -> str:
    if not Path(HWP5TXT).exists():
        raise RuntimeError("HWP 파일을 읽을 hwp5txt 가 없습니다(pip install pyhwp)")
    with tempfile.NamedTemporaryFile(suffix=".hwp") as f:
        f.write(data)
        f.flush()
        try:
            out = subprocess.run([HWP5TXT, f.name], capture_output=True, timeout=EXTRACT_TIMEOUT_SEC)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"HWP 글자 추출이 {EXTRACT_TIMEOUT_SEC}초 안에 끝나지 않음") from e
    if out.returncode != 0:
        raise RuntimeError(f"HWP 글자 추출 실패: {out.stderr[:200].decode('utf-8', 'replace')}")
    return out.stdout.decode("utf-8", "replace")


def pdf_text(data: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(data)
        f.flush()
        try:
            out = subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, timeout=EXTRACT_TIMEOUT_SEC)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"PDF 글자 추출이 {EXTRACT_TIMEOUT_SEC}초 안에 끝나지 않음") from e
    return out.stdout.decode("utf-8", "replace")


def xlsx_text(data: bytes) -> str:
    """엑셀 표(경찰청 범죄통계 등)를 '시트 / 행: 칸 칸 칸' 줄로. 숫자는 천 단위 쉼표를 붙여 원문 표기와 맞춘다."""
    import openpyxl  # 엑셀 첨부가 있을 때만 필요
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    lines = []
    for ws in wb.worksheets:
        lines.append(f"[시트] {ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells = [f"{v:,}" if isinstance(v, int) else str(v) for v in row if v is not None and str(v).strip()]
            if cells:
                lines.append(" ".join(cells))
    return "\n".join(lines)


def file_text(data: bytes) -> str:
    """PDF·HWP·HWPX·XLSX 의 글자. 그 밖이면 ''."""
    k = kind_of(data)
    if k == "pdf":
        return pdf_text(data)
    if k == "hwpx":
        return hwpx_text(data)
    if k == "hwp":
        return hwp_text(data)
    if k == "xlsx":
        return xlsx_text(data)
    return ""


JS_FUNC = re.compile(r"function\s+(\w+)\s*\(([^)]*)\)\s*\{(.*?)\}", re.S)
JS_CONCAT = re.compile(r"(?:[\"'][^\"']*[\"']|\w+)(?:\s*\+\s*(?:[\"'][^\"']*[\"']|\w+))+")
JS_CALL = re.compile(r"(\w+)\s*\(\s*((?:'[^']*'\s*,?\s*)+)\)")


def _js_templates(page_html: str) -> dict[str, tuple[list[str], str]]:
    """내려받기 함수: 이름 → (인자 이름들, 주소 만드는 식). 예) fn_downFile(atchFileId, fileSn) →
    "/cmm/fms/FileDown.do?atchFileId="+atchFileId+"&fileSn="+fileSn (11/14 KB경영연구소·여론조사심의위)."""
    out = {}
    for name, params, body in JS_FUNC.findall(page_html):
        args = [a.strip() for a in params.split(",") if a.strip()]
        for expr in JS_CONCAT.findall(body):
            if args and any(a in expr for a in args) and re.search(r"[\"'][^\"']*(?:down|file|atch)[^\"']*[\"']", expr, re.I):
                out[name] = (args, expr)
                break
    return out


def _js_url(call: str, templates: dict) -> str:
    """javascript: fn_downFile('FILE_1','0') → 함수 식에 값을 넣은 주소. 못 만들면 ''."""
    m = JS_CALL.search(call)
    if not m or m.group(1) not in templates:
        return ""
    args, expr = templates[m.group(1)]
    values = dict(zip(args, re.findall(r"'([^']*)'", m.group(2))))
    parts = []
    for tok in re.split(r"\s*\+\s*", expr):
        if tok[:1] in "\"'":
            parts.append(tok[1:-1])
        elif tok in values:
            parts.append(values[tok])
        else:
            return ""   # 모르는 변수가 들어간 식
    return "".join(parts)


def attachments(page_html: str, base_url: str) -> list[tuple[str, str]]:
    """게시물 페이지의 첨부파일 (이름, 주소). 같은 주소는 한 번만, 최대 MAX_ATTACHMENTS 개.
    보도자료·첨부라는 이름이 붙은 것을 앞에 둔다."""
    out, seen = [], set()
    links = [(a or b, label) for a, b, label in ANCHOR.findall(page_html)] + [(m.group(1) or m.group(3), m.group(2) or m.group(4) or "")
                                         for m in SCRIPT_FILE.finditer(page_html)]
    templates = _js_templates(page_html)
    onclicks = re.findall(r"<a\b[^>]*onclick=\"([^\"]*\(\s*'[^\"]*)\"[^>]*>(.*?)</a>", page_html, re.I | re.S)
    for href, label in links + onclicks:
        href = href.replace("&amp;", "&").strip()
        if templates and (href.startswith("javascript") or "(" in href):
            href = _js_url(href, templates) or href
        if href.startswith(("javascript", "#", "mailto")) or not ATTACH_HREF.search(href) or NOT_ATTACH.search(href):
            continue
        if "(" in href or '"' in label or "+" in label:
            continue   # 못 푼 자바스크립트 호출·스크립트 속 HTML 조각
        url = urllib.parse.urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        name = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", label)).strip() or url.rsplit("/", 1)[-1]
        if NOT_SOURCE_NAME.search(name + " " + href):
            continue
        out.append((name[:80], url))
    out.sort(key=lambda x: (not re.search(r"\.(hwpx?|pdf)|보도|자료|첨부|결과|통계", x[0]), x[0] in ("내려받기", "다운로드")))
    return out[:MAX_ATTACHMENTS]
