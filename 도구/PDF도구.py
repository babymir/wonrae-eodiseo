"""원 보고서 PDF 받기·글자 뽑기·숫자 후보 찾기·기준 시점 확인."""
import math
import re
import subprocess
from pathlib import Path

import 캐시
from 기관별 import Candidate, fetch_bytes
from 숫자표기 import to_regex, variants
from 표찾기 import table_hits

SECTION_AUTHOR = re.compile(r"\|\s*[가-힣·ㆍ]{2,15}\s*\|")   # 정기간행물 묶음 표지의 '| 곽윤경|' 꼴 저자 표시
SECTION_COVER_MIN_AUTHORS = 3

PDF_DIR = Path(__file__).resolve().parent.parent / "작업" / "원본PDF"
SUB_DIR = Path(__file__).resolve().parent.parent / "작업" / "임시"   # 게이트웨이에 보낼 부분 PDF
SEND_PAGES = 12           # 게이트웨이 모델에 보낼 핵심어 상위 쪽 수
HEAD_PAGES = 2            # 항상 같이 보낼 앞쪽(제목·요약)
LETTER_KEY_LEN = 15       # 숫자 뺀 인용 대조에 쓸 글자 수
EXTRACT_TIMEOUT_SEC = 120 # PDF 글자 추출 시간 제한(특이한 PDF 에서 멈추는 것 방지)
CONTEXT_CHARS = 150       # 숫자 앞뒤로 핵심어를 확인할 글자 수
QUOTE_CHARS = 220         # 모델에 보여 줄 원문 앞뒤 글자 수
TABLE_HEAD_CHARS = 400    # 표 숫자일 때 위쪽에서 표 제목·단위를 찾을 글자 수
PERIOD_PAGES = 2          # 기준 시점을 확인할 앞쪽 쪽 수
MAX_HITS = 40
SYNONYMS = {"코스피": ["KOSPI"], "코스닥": ["KOSDAQ"], "비정규직": ["비정규"], "정규직": ["정규"]}
PERCENT_UNIT = re.compile(r"단위\s*[:：]?[^)\n]{0,15}%|\(\s*%p?\s*\)")
# 영문 재무표 머리: '(in millions)', '$ in millions', '($M)' (11/14 넷리스트 'License revenue 44.9' = 4490만 달러)
UNIT_MARKS = {"%": PERCENT_UNIT,
              "million": re.compile(r"in\s+millions|\(\s*\$?\s*m(?:illions?|m|n)?\s*\)|\$\s*m\b|US\$\s*m", re.I),
              "billion": re.compile(r"in\s+billions|\(\s*\$?\s*b(?:illions?|n)?\s*\)|\$\s*bn?\b", re.I)}


def unit_ok(v, text: str) -> bool:
    """단위가 있어야 인정하는 맨 숫자 표기면 그 글에 단위 표시가 있는지."""
    return not v.need_unit or bool(UNIT_MARKS.get(v.need_unit, PERCENT_UNIT).search(text))
FUZZY_MIN_SCORE = 90      # 인용 3단계 검증(비슷한 글자 맞추기) 최소 점수
FUZZY_MIN_CHARS = 15
UNIT_HEAD = re.compile(r"\(단위\s*[:：][^)]{1,20}\)|<[^>]{2,40}>|\[[^\]]{2,40}\]")
YEAR = re.compile(r"(20\d{2})\s*년")
MONTH = re.compile(r"(\d{1,2})\s*월")
QUARTER = re.compile(r"(\d)\s*(?:/\s*4\s*)?분기")


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def keyword_hits(keywords: list[str], text: str) -> list[str]:
    flat = squash(text)
    return [k for k in keywords if any(squash(w) in flat for w in [k, *SYNONYMS.get(k, [])])]


def download_pdf(c: Candidate) -> Path | None:
    """PDF 를 받아 저장. 받은 게 PDF 가 아니면(HWP·엑셀·오류 페이지) None."""
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    path = PDF_DIR / f"{c.file_key}.pdf"
    if path.exists():  # 이미 받은 파일은 다시 받지 않음
        return path
    data = fetch_bytes(c.pdf_url)
    if not data.startswith(b"%PDF"):
        print(f"    PDF가 아니라 건너뜀: {c.pdf_url} (앞부분 {data[:20]!r})")
        return None
    path.write_bytes(data)
    return path


def _pdftotext(path: Path, layout: bool) -> list[str]:
    """pdftotext 로 쪽별 글자. layout=True 면 줄 맞춤(표 칸 위치 유지). 결과는 캐시에 저장."""
    kind = "쪽글자_줄맞춤" if layout else "쪽글자"
    key = 캐시.file_key(path)
    if (hit := 캐시.get(kind, key)) is not None:
        return hit
    cmd = ["pdftotext", *(["-layout"] if layout else []), str(path), "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=EXTRACT_TIMEOUT_SEC)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"PDF 글자 추출이 {EXTRACT_TIMEOUT_SEC}초 안에 끝나지 않음: {path.name}") from e
    if out.returncode != 0:
        raise RuntimeError(f"PDF 글자 추출 실패: {path.name} — {out.stderr[:200].decode('utf-8', 'replace')}")
    text = out.stdout.decode("utf-8", "replace")  # 깨진 글자가 섞인 PDF 가 있어 대체 문자로 바꾸고 계속
    pages = text.split("\f") if layout else [re.sub(r"\s+", " ", p) for p in text.split("\f")]
    if pages and not pages[-1].strip():
        pages.pop()
    캐시.put(kind, key, pages)
    return pages


def pdf_pages(path: Path) -> list[str]:
    return _pdftotext(path, layout=False)


def layout_pages(path: Path) -> list[str]:
    """줄 맞춤 쪽 글자(표 줄·열 위치 유지). 실패하면 빈 목록(기본 글자로 계속)."""
    try:
        return _pdftotext(path, layout=True)
    except RuntimeError as e:
        print(f"    줄 맞춤 추출 실패, 기본 글자로만 찾음: {e}")
        return []


# ── 기준 시점 ────────────────────────────────────────────────────────────
def period_tokens(period: str) -> dict:
    """'2026년 7월' → {'년': '2026', '월': '7'},  '2025년 4분기' → {'년': '2025', '분기': '4'}"""
    tokens = {}
    if m := YEAR.search(period or ""):
        tokens["년"] = m.group(1)
    if m := QUARTER.search(period or ""):
        tokens["분기"] = m.group(1)
    elif m := MONTH.search(period or ""):
        tokens["월"] = m.group(1)
    return tokens


# 외국 원문의 달 이름(영어·프랑스어). 11/14: 'October' 를 못 알아봐 IEA 11월 보고서를 '다른 달'로 버림
MONTH_NAMES = {1: "january|janvier|jan", 2: "february|février|fevrier|feb", 3: "march|mars|mar", 4: "april|avril|apr",
               5: "may|mai", 6: "june|juin|jun", 7: "july|juillet|jul", 8: "august|août|aout|aug",
               9: "september|septembre|sept|sep", 10: "october|octobre|oct", 11: "november|novembre|nov",
               12: "december|décembre|decembre|dec"}
QUARTER_NAMES = {1: "q1|first quarter|1st quarter|t1", 2: "q2|second quarter|2nd quarter|t2",
                 3: "q3|third quarter|3rd quarter|t3", 4: "q4|fourth quarter|4th quarter|t4"}


def period_score(pages: list[str], title: str, tokens: dict) -> int:
    """제목·앞 PERIOD_PAGES 쪽에 기준 시점(년·월·분기)이 몇 개 맞는지. 한국어 표기와 영어·프랑스어 표기 모두."""
    head = squash(title + " " + " ".join(pages[:PERIOD_PAGES]))[:4000]
    low = (title + " " + " ".join(pages[:PERIOD_PAGES]))[:6000].lower()   # 달 이름은 띄어쓰기를 살린 글자에서
    foreign = bool(re.search(r"[A-Za-z]{4}", head))
    score = 0
    if "년" in tokens and (f"{tokens['년']}년" in head or (foreign and re.search(rf"(?<!\d){tokens['년']}(?!\d)", head))):
        score += 1
    if "월" in tokens and (re.search(rf"(?<!\d){tokens['월']}월", head)
                          or re.search(rf"(?<![a-z])(?:{MONTH_NAMES.get(int(tokens['월']), '^$')})(?![a-z])", low)):
        score += 1
    if "분기" in tokens:
        # 분기 끝 달(3·6·9·12월) 월간 보도자료에도 분기 숫자가 실림: '3분기' ↔ '9월 산업활동동향'
        # (11/13 기존 #15: 3분기 소매판매를 9월호 47쪽에서 찾아야 하는데 시점 안 맞음으로 걸러짐)
        q = int(tokens["분기"])
        last_month = q * 3
        if (re.search(rf"(?<!\d){q}(/4)?분기", head) or re.search(rf"(?<!\d){last_month}월", head)
                or re.search(rf"(?<![a-z])(?:{QUARTER_NAMES.get(q, '^$')})(?![a-z0-9])", low)):
            score += 1
    return score


# ── 숫자 후보 찾기 + 순위 ────────────────────────────────────────────────
def _table_head(text: str, start: int) -> str:
    """숫자 위쪽 글에서 가장 가까운 단위 표시((단위: 천명) 등)를 찾는다."""
    above = text[max(0, start - TABLE_HEAD_CHARS): start]
    units = UNIT_HEAD.findall(above)
    return units[-1] if units else ""


def _scale_fit(kind: str, unit_text: str) -> bool | None:
    """표 숫자 표기(천·만·억 단위로 나눈 값)가 표 머리 '(단위: …)'와 맞는지.
    True 맞음 / False 다름(버림) / None 판단 못 함(단위 표시 없음·표 숫자 아님).
    근거: TAT-QA — 단위·배율은 흔히 표 머리에만 있다. 11/13: '23만 2천'↔ 표 '232 (단위: 천가구)'."""
    m = re.search(r"단위\s*(천|만|억)\)?$", kind.replace(")", ""))
    if not m:
        return None
    if not unit_text:
        return None
    want = m.group(1)
    if re.search(rf"{want}\s*(?:명|원|가구|개|건|톤|호|대|달러|㎡)?", unit_text) and want in unit_text:
        return True
    return False if re.search(r"명|원|가구|개|건|톤|달러", unit_text) else None


ROW_YEAR = re.compile(r"\[행\]\s*(20\d{2})(?!\d)")
ROW_NAMES = re.compile(r"\[(?:행 이름|열)\]\s*(\S+)")


def _row_check(context: str, keywords: list[str], period: dict | None) -> float | None:
    """표 줄 후보 확인. None 이면 버림(줄 연도가 기사 기준 연도와 다름), 아니면 점수 보정값.
    11/13 시험: '15만9천 명'이 다른 연도(2024) 줄의 '159'에 걸려 엉뚱한 판정. SciTab: 표 오류 절반이 엉뚱한 칸."""
    year = ROW_YEAR.search(context)
    if year and period and period.get("년") and year.group(1) != period["년"]:
        return None
    names = " ".join(ROW_NAMES.findall(context))
    words = [w for k in keywords for w in re.findall(r"[가-힣A-Za-z]{2,}", k)]
    return 0.3 if any(w in names for w in words) else -0.3


def find_candidates(pages: list[str], number: str, keywords: list[str],
                    layout: list[str] | None = None, period: dict | None = None) -> list[dict]:
    """모든 표기 후보로 숫자를 찾고, 핵심어 점수로 순위를 매긴다.
    점수 = 앞뒤 CONTEXT_CHARS 안 핵심어의 '드묾' 가중치 합(여러 쪽에 흔한 단어는 낮게).
    layout(줄 맞춤 글자)을 주면 표 줄도 찾는다: 표 제목·단위·열 머리·위 줄을 붙인 글에서 핵심어 확인."""
    doc_freq = {k: sum(1 for p in pages if keyword_hits([k], p)) for k in keywords}
    weight = {k: 1 / math.log(2 + n) for k, n in doc_freq.items() if n}
    hits, seen = [], set()
    words = sorted({w for k in keywords for w in k.split() if len(w) >= 2 and " " in k})
    for v in variants(number):
        pattern = to_regex(v.text)
        exact_bonus = 0.5 if v.kind == "정확" else 0.0
        for no, text in enumerate(pages, start=1):
            if layout and "표 숫자" in v.kind:
                continue  # 표 숫자 표기는 아래 표 줄 찾기로(펴진 글자에선 행·열 머리를 잃음)
            if not unit_ok(v, text):
                continue  # 맨 숫자(25.0)는 그 쪽에 '(단위: %)' 표시가 있을 때만
            for m in pattern.finditer(text):
                if (no, m.start()) in seen:
                    continue
                seen.add((no, m.start()))
                line_start = text.rfind("\n", 0, m.start()) + 1
                line_year = re.match(r"\s*(20\d{2})(?!\d)", text[line_start: m.start()])
                if line_year and period and period.get("년") and line_year.group(1) != period["년"]:
                    continue  # 다른 연도로 시작하는 줄(표가 글자로 펴진 것)의 같은 숫자
                around = text[max(0, m.start() - CONTEXT_CHARS): m.end() + CONTEXT_CHARS]
                kws = keyword_hits(keywords, around)
                # 핵심어 구절('3분기 매출')이 그대로 없으면 낱말('매출')로라도(점수는 낮게, 11/14 공시 손익표)
                part = [] if kws else keyword_hits(words, around)
                if not kws and not part:
                    continue
                hits.append({
                    "쪽": no, "표기": m.group(0), "종류": v.kind, "핵심어": kws or part,
                    "점수": round(sum(weight.get(k, 0) for k in kws) + 0.25 * len(part) + exact_bonus, 3),
                    "단위표시": _table_head(text, m.start()),
                    "원문": text[max(0, m.start() - QUOTE_CHARS): m.end() + QUOTE_CHARS],
                })
        for no, line_no, col, mark, context in table_hits(layout or [], pattern):
            if ("표", no, line_no, col) in seen or not unit_ok(v, context):
                continue
            seen.add(("표", no, line_no, col))
            # 표 머리는 짧은 낱말이라 '청년층 취업자' 같은 붙은 핵심어는 낱말로 나눠서도 본다(점수는 절반)
            kws = keyword_hits(keywords, context)
            part = [] if kws else keyword_hits(words, context)
            if not kws and not part:
                continue
            row_bonus = _row_check(context, keywords, period)
            if row_bonus is None:
                continue  # 다른 연도 줄의 같은 숫자
            unit = re.search(r"\(\s*단위[^)]*\)", context)
            scale_fit = _scale_fit(v.kind, unit.group(0) if unit else "")
            if scale_fit is False:
                continue  # 천 단위로 바꿔 찾은 숫자인데 표 단위가 '명'처럼 다름 → 다른 숫자
            hits.append({"쪽": no, "표기": mark.lstrip("-+△▽▲▼"), "종류": v.kind + " · 표 줄", "핵심어": kws or part,
                         "점수": round(sum(weight.get(k, 0) for k in kws) + 0.25 * len(part) + exact_bonus
                                       + (0.3 if scale_fit else -0.3 if scale_fit is None and "표 숫자" in v.kind else 0)
                                       + row_bonus, 3),
                         "단위표시": unit.group(0) if unit else "", "원문": context})
    hits.sort(key=lambda h: (-h["점수"], h["쪽"]))
    return hits[:MAX_HITS]


def quote_in_pages(quote: str, pages: list[str]) -> int | None:
    """모델이 인용한 문장이 PDF 글자에 실제로 있는 쪽(1부터). 없으면 None(지어낸 인용 차단).
    인용 전체가 아니라 앞 30글자(공백 제거)로 확인한다(줄바꿈·띄어쓰기 차이 대비)."""
    key = squash(quote)[:30]
    if len(key) < 8:
        return None
    for no, p in enumerate(pages, start=1):
        if key in squash(p):
            return no
    return None


def quote_fuzzy(quote: str, pages: list[str], number: str) -> int | None:
    """인용 3단계 검증: 띄어쓰기·줄바꿈·글자 몇 개가 달라도 비슷한 구간이 있고(점수 FUZZY_MIN_SCORE 이상),
    그 구간 안에 기사 숫자(표기 후보 중 하나)가 있으면 그 쪽(1부터). 지어낸 인용은 숫자 조건에서 걸러진다."""
    from rapidfuzz import fuzz  # 필요할 때만 불러옴
    q = squash(quote)
    if len(q) < FUZZY_MIN_CHARS:
        return None
    patterns = [to_regex(v.text) for v in variants(number)]
    for no, page in enumerate(pages, start=1):
        flat = squash(page)
        al = fuzz.partial_ratio_alignment(q, flat)
        if al is None or al.score < FUZZY_MIN_SCORE:
            continue
        span = flat[max(0, al.dest_start - 5): al.dest_end + 5]
        if any(p.search(span) for p in patterns):
            return no
    return None


def page_text_layout(pdf: Path, page_no: int) -> str:
    """한 쪽만 줄 맞춤(-layout) 방식으로 뽑는다(page_no 는 1부터)."""
    try:
        out = subprocess.run(["pdftotext", "-layout", "-f", str(page_no), "-l", str(page_no), str(pdf), "-"],
                             capture_output=True, timeout=EXTRACT_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        return ""
    return out.stdout.decode("utf-8", "replace") if out.returncode == 0 else ""


def letters_only(text: str) -> str:
    """숫자·기호·공백을 빼고 글자(한글·영문)만 남긴다."""
    return re.sub(r"[^가-힣A-Za-z]", "", text)


def quote_letters_on_page(quote: str, page: str) -> bool:
    """숫자가 그림 글자라 글자 추출에서 빠지는 PDF용 확인:
    인용에서 숫자를 뺀 글자(앞 LETTER_KEY_LEN자)가 그 쪽 글자에 있으면 인정."""
    key = letters_only(quote)[:LETTER_KEY_LEN]
    return len(key) >= 8 and key in letters_only(page)


# ── 게이트웨이 모델에 보낼 쪽 고르기(큰 PDF·긴 PDF 대비) ─────────────────
def pick_pages(pages: list[str], keywords: list[str]) -> list[int]:
    """핵심어가 드물게·많이 나오는 쪽 상위 SEND_PAGES개 + 앞 HEAD_PAGES쪽 (0부터 센 번호, 정렬)."""
    doc_freq = {k: sum(1 for p in pages if keyword_hits([k], p)) for k in keywords}
    weight = {k: 1 / math.log(2 + n) for k, n in doc_freq.items() if n}
    score = {i: sum(w for k, w in weight.items() if keyword_hits([k], p)) for i, p in enumerate(pages)}
    ranked = [i for i in sorted(score, key=lambda i: -score[i]) if score[i] > 0][:SEND_PAGES]
    return sorted(set(ranked) | set(range(min(HEAD_PAGES, len(pages)))))


def make_sub_pdf(pdf: Path, page_indexes: list[int]) -> Path:
    """고른 쪽만 담은 PDF 를 작업/임시 에 만든다(PyMuPDF)."""
    import fitz  # PyMuPDF: 필요할 때만 불러옴
    SUB_DIR.mkdir(parents=True, exist_ok=True)
    out = SUB_DIR / f"{pdf.stem}_부분_{len(page_indexes)}쪽.pdf"
    src, dst = fitz.open(pdf), fitz.open()
    for i in page_indexes:
        dst.insert_pdf(src, from_page=i, to_page=i)
    dst.save(out, garbage=4, deflate=True)
    return out


# ── 보고서 제목 비슷한 정도 ─────────────────────────────────────────────
def _bigrams(text: str) -> set[str]:
    t = letters_only(text)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def title_similarity(report_name: str, text: str) -> float:
    """기사가 밝힌 보고서 이름의 두 글자 묶음 중 후보 제목·앞쪽 글에 있는 비율(0~1).
    기간 표현(2026년 7월 등)은 기준 시점 확인에서 따로 보므로 뺀다."""
    name = re.sub(r"\d{4}\s*년|\d{1,2}\s*월|\d\s*분기|결과|보도자료|보고서", " ", report_name or "")
    want = _bigrams(name)
    if not want:
        return 0.0
    return len(want & _bigrams(text)) / len(want)


# ── 판정에 쓰는 보조 글 ──────────────────────────────────────────────────
OVERVIEW_WORDS = re.compile(r"대상으로|조사 ?대상|조사 ?기간|표본|기준 ?시점|조사 ?개요")
OVERVIEW_CHARS = 1200
SNIPPET_CHARS = 300


def survey_overview(pages: list[str]) -> str:
    """보고서에서 조사 대상·기간을 설명하는 문장을 앞에서부터 모은다(대상 판정 오탐 방지)."""
    found = []
    for page in pages:
        for m in OVERVIEW_WORDS.finditer(page):
            found.append(page[max(0, m.start() - 150): m.end() + 100])
            if sum(map(len, found)) > OVERVIEW_CHARS:
                return " … ".join(found)[:OVERVIEW_CHARS]
    return " … ".join(found) or "(없음)"


def snippets(page: str, words: list[str], limit: int) -> str:
    """쪽 글에서 핵심어 주변(앞뒤 SNIPPET_CHARS)만 이어 붙인다."""
    spans = sorted((max(0, m.start() - SNIPPET_CHARS), m.end() + SNIPPET_CHARS)
                   for w in words for m in re.finditer(re.escape(w), page))
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(e, merged[-1][1]))
        else:
            merged.append((s, e))
    return " … ".join(page[s:e] for s, e in merged)[:limit]


def periodical_article_title(pdf: Path) -> str:
    """정기간행물(보건복지포럼 등) 글 PDF 의 실제 글 제목. 아니면 "".
    1쪽이 그 호 '이달의 초점' 전체 목차(저자 표시 3개 이상)인 PDF 는 2쪽의 가장 큰 글씨가 이 글의 제목이다.
    11/13: 1쪽 목차에 다른 글 제목이 다 있어 제목 비교가 어느 글이든 맞다고 나옴(기존 #22 다른 글 선택).
    북마크(get_toc)는 확인한 보사연·자본연 PDF 4개 모두 없어 쓰지 않는다."""
    try:
        import fitz  # PyMuPDF(쪽 그림에도 씀). 없으면 이 보정만 건너뜀
        doc = fitz.open(pdf)
    except Exception as e:  # 열기 실패는 보정만 건너뛴다(이유 출력)
        print(f"    정기간행물 제목 확인 건너뜀: {e}")
        return ""
    with doc:
        if doc.page_count < 2 or len(SECTION_AUTHOR.findall(doc[0].get_text())) < SECTION_COVER_MIN_AUTHORS:
            return ""
        spans = [(sp["size"], sp["text"].strip()) for b in doc[1].get_text("dict")["blocks"]
                 for line in b.get("lines", []) for sp in line["spans"] if sp["text"].strip()]
    if not spans:
        return ""
    top = max(size for size, _ in spans)
    return " ".join(t for size, t in spans if size >= top - 0.5)[:120]
