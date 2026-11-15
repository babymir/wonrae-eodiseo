"""기업 숫자 → 금융감독원 전자공시(DART) 원문(11/14).

기업 실적·사업 숫자의 원문은 DART 분기·반기·사업보고서와 잠정실적 공시다. OpenDART API(무료 인증키,
.env 의 DART_API_KEY)로 회사 → 기사 날짜 전후 공시 → 원문(document.xml, zip 안 XML)을 받아 글자로 대조한다.
- 회사 목록(corpCode.xml, 약 12만 곳)은 작업/캐시/dart_회사목록.json 에 저장해 CORP_CACHE_DAYS 일마다 새로 받는다
- 원문 글자는 작업/웹문서/gen_dart_<접수번호>.txt (일반경로.fetch_doc 이 이미 받은 파일로 그대로 씀)
- 사람에게 보여 줄 주소: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=<접수번호>
"""
import html as html_lib
import io
import json
import re
import time
import urllib.parse
import zipfile
from datetime import date, timedelta
from pathlib import Path

from 기관별 import Candidate, fetch_bytes
from 일반경로 import WEB_DIR, normalize_numbers
from 후보문서 import article_day

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
API = "https://opendart.fss.or.kr/api/"
VIEW = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={no}"
CORP_CACHE = ROOT / "작업" / "캐시" / "dart_회사목록.json"
CORP_CACHE_DAYS = 7
DAYS_BEFORE, DAYS_AFTER = 120, 3   # 기사 날짜 앞뒤로 찾을 공시 기간
MAX_REPORTS = 3
REPORT_NAME = re.compile(r"분기보고서|반기보고서|사업보고서|영업\(잠정\)실적|매출액또는손익구조|결산실적")
# 실적이 아닌 회사 사건(주총·증자·계약 등): 그 사건 공시를 찾는다(11/14 무작위2: 오스코텍 임시주총 정관 변경)
EVENT_WORDS = {
    r"주주총회|주총|정관|이사\s*선임|감사\s*선임": r"주주총회|정관",
    r"유상증자|무상증자|증자": r"증자",
    r"자사주|자기주식": r"자기주식",
    r"합병|분할|인수": r"합병|분할|양수|인수",
    r"배당": r"배당",
    r"공급계약|수주|계약\s*체결": r"공급계약|계약",
    r"전환사채|CB|신주인수권": r"전환사채|신주인수권",
}
MONEY_WORDS = re.compile(r"매출|영업이익|영업손실|순이익|순손실|당기순|실적|분기|반기|보고서|공시|흑자|적자|고객|배당|자산|부채")
NOT_COMPANY = re.compile(r"거래소|공사|공단|협회|연구원|연구소|정부|위원회|증권$|투자증권|[청부처]$")


def _key() -> str:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("DART_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def _api(path: str, **params) -> bytes:
    key = _key()
    if not key:
        raise RuntimeError("DART 인증키가 없습니다(.env 에 DART_API_KEY=… 를 넣어 주세요)")
    return fetch_bytes(API + path + "?" + urllib.parse.urlencode({"crtfc_key": key, **params}))


def _corps() -> dict[str, list]:
    """회사 이름 → [고유번호, 종목코드, 같은 이름의 모든 고유번호]. 상장사를 우선."""
    if CORP_CACHE.exists() and time.time() - CORP_CACHE.stat().st_mtime < CORP_CACHE_DAYS * 86400:
        cached = json.loads(CORP_CACHE.read_text(encoding="utf-8"))
        if all(len(v) == 3 for v in list(cached.values())[:5]):   # 옛 형식(고유번호 목록 없음)이면 새로 받는다
            return cached
    z = zipfile.ZipFile(io.BytesIO(_api("corpCode.xml")))
    xml = z.read(z.namelist()[0]).decode("utf-8", "replace")
    corps: dict[str, list[str]] = {}
    for code, name, stock in re.findall(r"<corp_code>(\d+)</corp_code>\s*<corp_name>([^<]+)</corp_name>\s*"
                                        r"(?:<corp_eng_name>[^<]*</corp_eng_name>\s*)?<stock_code>([^<]*)</stock_code>", xml):
        name, stock = name.strip(), stock.strip()
        if name not in corps:
            corps[name] = [code, stock, [code]]
        else:
            corps[name][2].append(code)   # 이름이 같은 회사가 여럿(옛 법인·새 법인) — 공시가 있는 쪽을 찾을 때 씀
            if stock and not corps[name][1]:
                corps[name][:2] = [code, stock]
    CORP_CACHE.parent.mkdir(parents=True, exist_ok=True)
    CORP_CACHE.write_text(json.dumps(corps, ensure_ascii=False), encoding="utf-8")
    print(f"    DART 회사 목록 새로 받음: {len(corps)}곳")
    return corps


def find_corp(name: str) -> tuple[str, str] | None:
    """(고유번호, 정식 이름). 이름이 정확히 같거나 '(주)'·'주식회사'를 뗀 이름이 같을 때만."""
    clean = re.sub(r"\(주\)|㈜|주식회사|\s", "", name or "")
    if not clean:
        return None
    corps = _corps()
    if NOT_COMPANY.search(clean) and not (corps.get(clean) or ["", ""])[1]:
        return None   # 공사·공단이라도 상장사(한국전력공사 등)는 DART 공시가 원문(11/16 신규 세트2)
    hit = corps.get(clean) or corps.get(name.strip())
    if hit:
        return hit[0], clean
    return _listed_prefix(clean, corps)


PREFIX_MIN_CHARS = 4      # 앞부분 맞추기: 상장사 이름이 이 글자 수 이상이고
PREFIX_MIN_SHARE = 0.6    # 기사 속 이름의 이 비율 이상일 때만(짧은 이름이 우연히 맞는 것을 막음)
GROUP_TAIL = re.compile(r"(그룹|계열|계열사)$")


def _listed_prefix(clean: str, corps: dict) -> tuple[str, str] | None:
    """정확히 같은 이름이 없을 때: 기사 속 이름의 앞부분이 상장사 이름이면 그 회사(가장 긴 것).
    예(11/16 사전 점검 무작위 #24): 기사 '리가켐바이오사이언스' ↔ DART 등록 이름 '리가켐바이오'."""
    if GROUP_TAIL.search(clean):
        return None   # '현대자동차그룹'은 회사 하나가 아니라 그룹 — 계열사 공시를 원문으로 보지 않음
    best = ""
    for cname, (_, stock, _codes) in corps.items():
        if (stock and len(cname) > len(best) and clean.startswith(cname) and len(cname) >= PREFIX_MIN_CHARS
                and len(cname) >= PREFIX_MIN_SHARE * len(clean)):
            best = cname
    if not best:
        return None
    print(f"    DART 회사 이름: '{clean}'과 같은 이름이 없어 앞부분이 같은 상장사 '{best}'로 찾음")
    return corps[best][0], best


PARTICLE = re.compile(r"(?:의|은|는|이|가|도|과|와|에|을|를|로|으로)$")


def corp_in_sentence(sentence: str) -> tuple[str, str] | None:
    """문장 속 상장회사 이름(어절, 조사 뗌, 이웃 두 어절 붙여서도). '금융감독원 전자공시에 따르면 두산 건설의 …'처럼
    분해한 기관이 회사가 아닐 때 쓴다(11/15 검증 세트). 우연히 같은 짧은 이름을 피하려고 상장사·3글자 이상만."""
    corps = _corps()
    words = [PARTICLE.sub("", w) for w in re.findall(r"[가-힣A-Za-z0-9&]+", sentence)]
    for cand in [a + b for a, b in zip(words, words[1:])] + words:
        hit = corps.get(cand)
        if len(cand) >= 3 and hit and hit[1] and not NOT_COMPANY.search(cand):
            return hit[0], cand
    return None


def is_company_number(info: dict, sentence: str) -> bool:
    return bool(MONEY_WORDS.search(sentence)) and find_corp(info.get("기관", "")) is not None


UNIT_MARK = re.compile(r"단위\s*[:：]?\s*(백만원|천원|원)")
UNIT_SIZE = {"원": 1, "천원": 10**3, "백만원": 10**6}
BIG_NUMBER = re.compile(r"(?<![\d.,])\(?-?\d{1,3}(?:,\d{3}){1,5}\)?(?![\d,])")


def korean_money(won: int) -> str:
    """원 단위 정수 → 기사식 표기('1조246억', '19억3300만'). 0인 묶음은 뺀다."""
    jo, rest = divmod(won, 10**12)
    eok, rest = divmod(rest, 10**8)
    man = rest // 10**4
    return (f"{jo}조" if jo else "") + (f"{eok}억" if eok else "") + (f"{man}만" if man else "")


def money_forms(won: int) -> list[str]:
    """기사가 쓸 법한 반올림·버림 표기들(억 반올림·버림, 천만·백만 반올림)."""
    forms = []
    for step, how in ((10**8, round), (10**8, int), (10**7, round), (10**6, round)):
        v = how(won / step) * step
        if v >= 10**8:
            forms.append(korean_money(v) + "원")
    return list(dict.fromkeys(forms))


def add_money_forms(text: str) -> str:
    """재무표 숫자 옆에 '〔환산 1,352억원·1352억원〕'을 붙인다(11/14 무작위3: 쏘카 '1352억원' ↔ '135,195,115,866'(단위 원)).
    표 머리 '(단위 : 원/천원/백만원)'을 보고 그 뒤 숫자를 원으로 바꿔 억 이상만."""
    marks = [(m.start(), UNIT_SIZE[m.group(1)]) for m in UNIT_MARK.finditer(text)]
    if not marks:
        return text
    out, last, mi, size = [], 0, 0, 0
    for m in BIG_NUMBER.finditer(text):
        while mi < len(marks) and marks[mi][0] < m.start():
            size = marks[mi][1]
            mi += 1
        if not size:
            continue
        won = int(re.sub(r"[^\d]", "", m.group())) * size
        if won < 10**8:
            continue
        out.append(text[last:m.end()] + "〔환산 " + "·".join(money_forms(won)) + "〕")
        last = m.end()
    return "".join(out) + text[last:]


TABLE = re.compile(r"<TABLE\b.*?</TABLE>", re.S | re.I)
ROW = re.compile(r"<TR\b.*?</TR>", re.S | re.I)
CELL = re.compile(r"<(TH|TD|TE|TU)\b([^>]*)>(.*?)</\1>", re.S | re.I)
NUM_CELL = re.compile(r"^\(?-?[\d,.]+\)?$")


def _cell_text(raw: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", raw))).replace("\u3000", " ").strip()


def flatten_table(xml: str) -> str:
    """공시 표 한 개 → 줄마다 '행 이름 | 열 이름: 값 | …'(11/14 마키나락스: 표가 펴지면 '3개월·누적' 열 머리가
    값과 떨어져 판정 모델이 어느 열 숫자인지 몰랐음). 열 이름은 표 머리(THEAD 또는 칸이 모두 TH 인 앞줄)에서만 만든다.
    머리가 없는 표(신고서 양식 등)는 칸을 ' | '로 이은 줄 그대로."""
    grid: list[list[str]] = []
    is_head: list[bool] = []
    carry: dict[int, tuple[str, int]] = {}   # 열 번호 → (글자, 남은 줄 수) — ROWSPAN
    thead_end = xml.upper().find("</THEAD>")

    def take_carry(cells: list[str], col: int) -> int:
        nonlocal carry
        while col in carry:
            text, left = carry[col]
            cells.append(text)
            carry = {**carry, col: (text, left - 1)} if left > 1 else {k: v for k, v in carry.items() if k != col}
            col += 1
        return col

    for m in ROW.finditer(xml):
        row = m.group(0)
        found = CELL.findall(row)
        cells, col = [], 0
        for _, attrs, raw in found:
            col = take_carry(cells, col)
            text = _cell_text(raw)
            span = int((re.search(r'COLSPAN="(\d+)"', attrs, re.I) or [0, 1])[1])
            rows = int((re.search(r'ROWSPAN="(\d+)"', attrs, re.I) or [0, 1])[1])
            for _ in range(span):
                cells.append(text)
                if rows > 1:
                    carry[col] = (text, rows - 1)
                col += 1
        take_carry(cells, col)
        if cells:
            grid.append(cells)
            in_thead = 0 <= m.start() < thead_end
            is_head.append(in_thead or (bool(found) and all(t.upper() == "TH" for t, _, _ in found)))
    n_head = next((i for i, h in enumerate(is_head) if not h), len(grid))
    if n_head == 0 or n_head >= len(grid):   # 머리 없는 표 / 머리뿐인 표: 칸을 이은 줄 그대로
        return "\n" + "\n".join(" | ".join(dict.fromkeys(c for c in r if c)) for r in grid) + "\n"
    names = {i: " ".join(dict.fromkeys(r[i] for r in grid[:n_head] if i < len(r) and r[i]))
             for i in range(1, max(len(r) for r in grid))}
    lines = []
    for r in grid[n_head:]:
        vals = [f"{names.get(i) + ': ' if names.get(i) else ''}{c}" for i, c in enumerate(r[1:], start=1) if c]
        lines.append(" | ".join([r[0], *vals]) if vals else r[0])
    return "\n" + "\n".join(lines) + "\n"


def _doc_text(rcept_no: str) -> str:
    z = zipfile.ZipFile(io.BytesIO(_api("document.xml", rcept_no=rcept_no)))
    parts = []
    for n in z.namelist():
        xml = z.read(n).decode("utf-8", "replace")
        xml = TABLE.sub(lambda m: flatten_table(m.group(0)), xml)   # 표는 줄마다 '행 | 열: 값'
        xml = re.sub(r"</(?:P|TR|TITLE|TABLE)>", "\n", xml, flags=re.I)   # 문단·표 줄 끝에서 줄바꿈
        parts.append(re.sub(r"<[^>]+>", " ", xml))
    text = html_lib.unescape("\n".join(parts))
    text = re.sub(r"영업수익|수익\(매출액\)", "영업수익(매출액)", text)   # 기사는 '매출'이라 씀
    return add_money_forms(normalize_numbers(re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text))))


def _past_year(period: str, day: date) -> int | None:
    """기준 시점이 기사 날짜보다 앞선 한 해 전체('2025년')면 그 연도. 월·분기·반기가 있으면 None."""
    m = re.fullmatch(r"\s*(20\d{2})\s*년?\s*", period or "")
    return int(m.group(1)) if m and int(m.group(1)) < day.year else None


def search(info: dict, sentence: str, article_date: str) -> list[Candidate]:
    """기사 날짜 앞뒤 공시 중 정기보고서·잠정실적 원문(최신부터 MAX_REPORTS 개)."""
    try:
        corp = find_corp(info.get("기관", ""))
        if not corp or not (_corps().get(corp[1]) or ["", ""])[1]:
            # 분해한 기관이 회사가 아니거나 비상장 이름(예: DART 목록의 '금융감독원')이면 문장 속 상장회사로
            corp = corp_in_sentence(sentence) or corp
        events = [name for words, name in EVENT_WORDS.items() if re.search(words, sentence)]
        if not corp or not (MONEY_WORDS.search(sentence) or events):
            return []
        day = article_day(article_date) or date.today()
        bgn, end = day - timedelta(days=DAYS_BEFORE), day + timedelta(days=DAYS_AFTER)
        codes = (_corps().get(corp[1]) or [corp[0], "", [corp[0]]])[2]
        codes = [corp[0], *[c for c in codes if c != corp[0]]][:4]
        for code in codes:   # 이름이 같은 회사가 여럿이면 기간 안에 공시가 있는 쪽(11/15: 두산건설 옛 법인은 공시 0건)
            data = json.loads(_api("list.json", corp_code=code, bgn_de=bgn.strftime("%Y%m%d"),
                                   end_de=end.strftime("%Y%m%d"), page_count=100))
            if data.get("list"):
                break
        # 기준 시점이 지난해 한 해 전체('지난해 매출')면 그해 사업보고서(다음 해 1~5월 제출)도 찾는다
        # (11/15 검증 세트: 매일유업 중국법인 지난해 실적 → 3월 사업보고서가 120일 창 밖이라 못 찾음)
        year = _past_year(info.get("기준시점", ""), day)
        if year:
            annual = json.loads(_api("list.json", corp_code=code, bgn_de=f"{year + 1}0101", end_de=f"{year + 1}0531",
                                     pblntf_ty="A", page_count=20))
            extra = [x for x in annual.get("list", []) if "사업보고서" in x.get("report_nm", "")]
            if extra:
                print(f"    DART '{corp[1]}' {year}년 사업보고서 {len(extra)}건 더함")
                data = {**data, "status": "000", "list": extra + list(data.get("list", []))}
    except (RuntimeError, ValueError, zipfile.BadZipFile) as e:  # DART 실패는 다른 경로로 계속(이유 출력)
        print(f"    DART 공시 찾기 실패: {e}")
        return []
    if data.get("status") not in ("000", "013"):   # 013 = 조회된 데이터 없음
        print(f"    DART 공시 목록 오류: {data.get('status')} {data.get('message')}")
        return []
    wanted = re.compile("|".join([REPORT_NAME.pattern, *events])) if events else REPORT_NAME
    # 사건 공시가 있으면 그것을 먼저(정기보고서는 뒤로)
    reports = sorted([x for x in data.get("list", []) if wanted.search(x.get("report_nm", ""))],
                     key=lambda x: not (events and re.search("|".join(events), x.get("report_nm", ""))))[:MAX_REPORTS]
    print(f"    DART '{corp[1]}' 공시 {len(data.get('list', []))}건 중 보고서·실적 {len(reports)}건")
    out = []
    for x in reports:
        report = re.sub(r"\s+", " ", x["report_nm"]).strip()   # 공시 이름 속 긴 공백 정리
        no, title = x["rcept_no"], f"{corp[1]} {report} ({x['rcept_dt']})"
        key = f"gen_dart_{no}"
        path = WEB_DIR / f"{key}.txt"
        if not path.exists():
            try:
                text = _doc_text(no)
            except (RuntimeError, zipfile.BadZipFile) as e:
                print(f"    DART 원문 받기 실패({title}): {e}")
                continue
            WEB_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        out.append(Candidate(title, VIEW.format(no=no), VIEW.format(no=no), key))
    return out


RCP_NO = re.compile(r"rcpNo=(\d{14})|rcept_no=(\d{14})")


def candidate_from_url(url: str, title: str = "") -> Candidate | None:
    """DART 공시 보기 주소(…rcpNo=접수번호) → OpenDART 원문을 받아 둔 후보. DART 주소가 아니면 None
    (라이너 Deep Research 가 지목한 공시를 우리가 직접 읽어 확인할 때, 11/15)."""
    m = RCP_NO.search(url or "")
    if not m:
        return None
    no = m.group(1) or m.group(2)
    key = f"gen_dart_{no}"
    path = WEB_DIR / f"{key}.txt"
    if not path.exists():
        try:
            text = _doc_text(no)
        except (RuntimeError, zipfile.BadZipFile) as e:
            print(f"    DART 원문 받기 실패({no}): {e}")
            return None
        WEB_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return Candidate(title or f"DART 공시 {no}", VIEW.format(no=no), VIEW.format(no=no), key)

