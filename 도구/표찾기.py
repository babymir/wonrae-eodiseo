"""표 안 숫자 찾기: 줄 맞춤(pdftotext -layout) 글자에서 숫자가 든 표 줄을 찾고,
표 제목·단위·열 머리·위 줄(연도 같은 행 머리)을 붙여 '읽을 수 있는 후보 문장'을 만든다.

기본 추출은 표를 한 줄로 펴 버려 숫자 앞뒤 150자에 '청년층' 같은 행·열 이름이 없다(11/12 새 기사 #12).
예) 2025년 4분기 적자가구 비율 25.0% →
    "[표] 1.7. 전국가구 소득 5분위별 적자가구 비율 추이 (단위: %) [열 머리] 구분 전체 1분위 … [열] 전체
     [위 줄] 2025 1/4 … [행] 4/4 25.0 58.7 …"
"""
import re

TITLE_LINE = re.compile(r"^\s*(?:\d+(?:[-.]\d+)*\.\s*\S|<\s*표|\[\s*표|표\s*\d|【)")
UNIT_LINE = re.compile(r"\(\s*단위\s*[:：]?[^)]{0,20}\)")
NUM_TOKEN = re.compile(r"[-+△▽▲▼]?\d[\d,]*(?:\.\d+)?")
LOOK_UP_LINES = 150     # 표 제목·단위를 찾을 위쪽 줄 수
ABOVE_ROWS = 3          # 같이 보여 줄 바로 위 줄 수(연도 같은 행 머리가 위에 있는 표)
MIN_ROW_NUMBERS = 3     # 표 줄로 볼 최소 숫자 개수
ROW_CHARS = 160


def _is_row(line: str) -> bool:
    return len(NUM_TOKEN.findall(line)) >= MIN_ROW_NUMBERS


def _is_header(line: str) -> bool:
    """글자가 많고 숫자가 적은 줄(열 머리 후보)."""
    letters = len(re.findall(r"[가-힣A-Za-z]", line))
    digits = len(re.findall(r"\d", line))
    return letters >= 4 and digits <= letters


def _column_name(header: str, start: int, end: int) -> str:
    """열 머리 줄에서 숫자 위치(start~end) 바로 위에 있는 낱말."""
    center = (start + end) / 2
    best, gap = "", 1e9
    for m in re.finditer(r"\S+", header):
        d = abs((m.start() + m.end()) / 2 - center)
        if d < gap:
            best, gap = m.group(0), d
    return best if gap < 12 else ""


def table_context(lines: list[str], row_idx: int, start: int, end: int) -> str:
    """row_idx 줄의 start~end 숫자에 표 제목·단위·열 머리·위 줄을 붙인 글."""
    title = unit = header = ""
    for i in range(row_idx - 1, max(-1, row_idx - LOOK_UP_LINES), -1):
        line = lines[i]
        if not unit and UNIT_LINE.search(line):
            unit = UNIT_LINE.search(line).group(0)
        if not header and _is_header(line) and not TITLE_LINE.match(line):
            header = line
        if TITLE_LINE.match(line) and _is_header(line):
            title = line.strip()
            break
    column = _column_name(header, start, end) if header else ""
    row_name = re.match(r"\s*([^\d△▽▲▼+\-]{1,30})", lines[row_idx])   # 줄 앞 글자 = 행 이름(예: '청년층', '제 주')
    row_label = re.sub(r"\s+", "", row_name.group(1)) if row_name else ""
    above = [lines[i].strip() for i in range(max(0, row_idx - ABOVE_ROWS), row_idx) if lines[i].strip()]
    parts = [f"[표] {title} {unit}".strip() if (title or unit) else "",
             f"[열 머리] {re.sub(r'\s+', ' ', header.strip())}" if header else "",
             f"[열] {column}" if column else "",
             f"[행 이름] {row_label}" if row_label else "",
             f"[위 줄] {' / '.join(re.sub(r'\s+', ' ', a)[:ROW_CHARS] for a in above)}" if above else "",
             f"[행] {re.sub(r'\s+', ' ', lines[row_idx].strip())[:ROW_CHARS]}"]
    return " ".join(p for p in parts if p)


def table_hits(layout_pages: list[str], pattern: re.Pattern) -> list[tuple[int, int, int, str, str]]:
    """표 줄에서 pattern(숫자 표기)을 찾아 [(쪽, 줄 번호, 줄 안 위치, 찾은 표기, 표 문맥 글)] 을 돌려준다."""
    out = []
    for no, page in enumerate(layout_pages, start=1):
        lines = page.split("\n")
        for idx, line in enumerate(lines):
            if not _is_row(line):
                continue
            for m in pattern.finditer(line):
                out.append((no, idx, m.start(), m.group(0), table_context(lines, idx, m.start(), m.end())))
    return out


def numbers_of(text: str) -> list[str]:
    return [n.lstrip("+△▽▲▼").replace(",", "") for n in NUM_TOKEN.findall(text)]


def find_number_row(quote: str, layout_page: str) -> int | None:
    """숫자만 있는 인용("10 139 7 -203 …")이 그 쪽의 어느 표 줄과 같은지(줄 번호). 없으면 None.
    인용의 숫자 3개 이상이 한 줄에 같은 순서로 이어져 있어야 인정."""
    want = numbers_of(quote)
    if len(want) < MIN_ROW_NUMBERS:
        return None
    for idx, line in enumerate(layout_page.split("\n")):
        have = numbers_of(line)
        for s in range(len(have) - len(want) + 1):
            if have[s:s + len(want)] == want:
                return idx
    return None
