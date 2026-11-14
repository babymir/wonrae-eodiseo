"""라이너 모델에 묻는 단계: 문장 분해, 후보 중 고르기·판정, 계산 확인, PDF 직접 읽기."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import 캐시
from liner_api import PDF_MAX_BYTES, ask_json, json_schema
from PDF도구 import (HEAD_PAGES, SEND_PAGES, page_text_layout, make_sub_pdf, pick_pages,
                   quote_fuzzy, quote_in_pages, quote_letters_on_page, survey_overview)
from 숫자표기 import to_regex, variants
from 표찾기 import find_number_row, table_context
import 출처종류

CHOOSE_CACHE_HOURS = 24 * 30   # 같은 판정 질문의 모델 답을 다시 쓸 기간
TOP_K = 5                  # 모델에 보여 줄 숫자 후보 수(11/14 밤: 3 → 5, 공시 표는 같은 숫자가 여러 줄에 나옴)
MIN_KEYWORD_PAGES = 2      # 핵심어 쪽이 이보다 적으면 PDF 전체를 보냄(크기 허용 시)
PAGE_HEAD_CHARS = 300      # PDF 직접 읽기로 찾은 쪽의 머리글(표 제목·단위)을 판정 모델에 같이 보여 줄 글자 수
WHOLE_SEND_MAX_PAGES = 60  # 전체를 보낼 수 있는 최대 쪽 수
MAX_PAGES_FOR_CALC = 6
PAGE_CHARS_FOR_CALC = 1500
S = {"type": "string"}
EXTRACT_SCHEMA = json_schema("extract", {"기관": S, "보고서": S, "숫자": S, "핵심어": {"type": "array", "items": S},
                                    "기준시점": S, "기관누리집": S, "원어검색어": S})
# 2단 판정(11/13 밤): 숫자 일치는 코드가 이미 확정(표기 후보·인용 검증). 모델은 칸마다 같음/동의어/다름만 답하고
# 판정은 코드가 정한다 — '다름'이 하나라도 있으면 표현 바뀜. 근거: QuanTemp·ClaimDecomp(좁은 분해), NumPert(표기 차이에 과민)
AXES = ["지표", "대상", "시점", "비교기준", "수준변화"]
AXIS_LABEL = {"지표": "용어", "대상": "대상", "시점": "기간", "비교기준": "기간", "수준변화": "용어"}
_AXIS = json_schema("axis", {"판단": {"type": "string", "enum": ["같음", "동의어", "다름", "알수없음"]},
                             "기사": S, "원문": S})["json_schema"]["schema"]
CHOOSE_SCHEMA = json_schema("choose", {
    "선택": {"type": "integer"}, "칸": json_schema("axes", {a: _AXIS for a in AXES})["json_schema"]["schema"],
    "원문인용": S, "확인못한부분": S})
PDF_FIND_SCHEMA = json_schema("pdf_find", {"찾음": {"type": "boolean"}, "쪽": {"type": "integer"}, "원문인용": S,
                                        "표제목": S, "행머리": S, "열머리": S})

EXTRACT_PROMPT = """다음 기사 문장을 '누가·무엇·언제·값'으로 분해해 JSON 하나로만 답하라. 설명 금지.
키: "기관"(숫자를 낸 연구원·기관 정식 이름), "보고서"(보고서·조사·통계 제목, 모르면 ""),
"숫자"(확인할 핵심 숫자 하나, 문장에 쓰인 그대로 단위 포함),
"핵심어"(숫자의 뜻을 나타내는 짧은 명사 2~4개 배열. 원문 보고서에 나올 법한 말로),
"기준시점"(숫자가 가리키는 조사 기준 기간. 예: "2026년 7월", "2025년 4분기", "2024년".
           '지난달·지난해·올해 2분기'는 기사 날짜({today_hint})로 계산. 모르면 ""),
"기관누리집"(그 기관의 공식 누리집 도메인만. 예: "기관이름.go.kr", "기관이름.or.kr", "agency.gov", "institute.org".
           확실히 아는 것만, 모르면 ""),
"원어검색어"(기관이 외국 기관이거나 숫자가 학술 논문·연구 결과면, 원 자료를 그 나라 말(보통 영어)로 찾을 검색어:
           자료 이름·지표·시점, 논문이면 연구 대상·주제 핵심 영어 낱말 4~8개(기사에 학술지 이름이 있으면 그 영어 이름도). 예: "Consumer Confidence Index March 2025",
           "statin use dementia risk cohort Journal name". 일본·중국 기관이면 그 나라 글자로
           (예: "観光白書 訪日外国人 旅行消費額"). 한국 기관 자료면 "")
문장: "{sentence}"
기관·보고서 이름이 문장에 없으면 아래 기사 문맥(제목·앞뒤 문단)에서 찾아라. 숫자는 반드시 위 문장에서 고른다.
기사 문맥: "{context}\""""

CHOOSE_PROMPT = """기사 문장의 숫자가 원 보고서의 어느 후보와 같은 것을 가리키는지 고르고, 칸별로 비교하라.
JSON 하나로만 답하라. 설명 금지.
기사: "{article}"
기사 숫자: "{number}"
원 보고서 후보(같은 숫자가 나온 곳 — 숫자 값은 코드가 이미 맞춰 봤다):
{candidates}
원문의 조사 개요(같은 보고서 다른 부분): "{overview}"
규칙:
- "선택": 기사 숫자와 같은 지표·같은 대상을 가리키는 후보 번호. 숫자만 같고 다른 지표면 0.
- "칸": 고른 후보와 기사를 아래 다섯 칸으로 따로 비교한다. 각 칸 {{"판단": "같음|동의어|다름|알수없음", "기사": "기사 표현", "원문": "원문 표현 그대로"}}
  · 지표: 무엇을 잰 숫자인가(비율·인원·금액·지수 등)
  · 대상: 누구·무엇에 대한 숫자인가(모집단·지역·연령)
  · 시점: 언제 숫자인가(연도·월·분기)
  · 비교기준: 무엇과 비교했나(전년동월·전월 등). 비교가 없는 숫자면 "같음"
  · 수준변화: 수준(~로, ~이다)인가 변화량(~만큼 늘어, %p 상승)인가
- 같은 값이 다른 표기·어림·반올림·단위 환산으로 쓰일 수 있다(43%↔43.0%, 91%↔91.17%, 867만1천명↔8,671천명). 표기 차이는 "다름"이 아니다.
- '1년 전보다'·'지난해 같은 달보다'와 '전년동월대비'처럼 뜻이 같은 말은 "동의어"다.
- 원문이 '전체 응답 기관'처럼 줄여 써도 조사 개요상 같은 대상이면 "같음".
- 원문에서 그 칸을 확인할 수 없으면 "알수없음"(다름이 아니다).
- 기사에 숫자와 별개인 다른 주장(예: "15년 만에 최대")은 칸에 넣지 말고 "확인못한부분"에 적어라.
- "원문인용": 고른 후보에서 숫자가 나온 문장(또는 표 줄) 그대로
선택이 0이면 칸은 모두 "알수없음", 원문인용은 비워라."""


PDF_FIND_PROMPT = """첨부한 PDF는 기사가 인용한 원 보고서 후보다. 기사 문장의 숫자가 이 PDF의 어디에 있는지 찾아라.
기사: "{article}"
기사 숫자: "{number}" (원문에서는 표기가 다를 수 있음: 단위 환산, 표 안 숫자, 소수 자리)
JSON 하나로만 답하라: {{"찾음": true/false, "쪽": 첨부 PDF의 쪽 번호(1부터), "원문인용": "숫자가 나온 문장 또는 표 줄을 PDF 글자 그대로",
"표제목": "표 안 숫자면 표 제목·단위, 아니면 \"\"", "행머리": "표 안 숫자면 그 행 이름(연도·분기·집단), 아니면 \"\"",
"열머리": "표 안 숫자면 그 열 이름, 아니면 \"\""}}
같은 지표·같은 기간이 아니면 "찾음": false. 지어내지 말 것."""


def extract(sentence: str, context: str, today_hint: str) -> dict:
    # 단순한 뽑기 → 생각 없이·가벼운 모델(11/13 시험: 2.9초→1.8초)
    info = ask_json(EXTRACT_PROMPT.format(sentence=sentence, context=context or "(없음)",
                                          today_hint=today_hint or "모름"),
                    kind="문장 분해", effort="none", routing="cost", schema=EXTRACT_SCHEMA)
    if not info.get("숫자"):
        raise RuntimeError(f"라이너 모델이 확인할 숫자를 뽑지 못했습니다: {info}")
    info["기관"] = info.get("기관") or ""  # 기관을 모르면 원문찾기.py 에서 '원출처 미확인'으로 답한다
    info["핵심어"] = [k for k in info.get("핵심어", []) if isinstance(k, str) and k.strip()]
    info["기준시점"] = info.get("기준시점") or ""
    info["기관누리집"] = info.get("기관누리집") or ""
    info["원어검색어"] = info.get("원어검색어") or ""
    return info


def choose(sentence: str, number: str, hits: list[dict], pages: list[str]) -> dict | None:
    """상위 TOP_K 후보를 모델에 보여 주고 같은 지표인 후보를 고르게 한다. 없으면 None."""
    top = hits[:TOP_K]
    lines = []
    for i, h in enumerate(top, start=1):
        unit = f" 단위표시 {h['단위표시']}" if h["단위표시"] else ""
        lines.append(f"[{i}] {h['쪽']}쪽 (원문 표기 '{h['표기']}'{unit}): \"{h['원문']}\"")
    # 판정은 품질 쪽 모델로 묶는다(라우터가 매번 다른 모델을 고르면 판정이 흔들림)
    prompt = CHOOSE_PROMPT.format(article=sentence, number=number, candidates="\n".join(lines),
                                  overview=survey_overview(pages))
    # 같은 문장·같은 후보에는 같은 판정(11/14 흔들림): 모델 답을 저장해 다시 쓴다(지시문이 바뀌면 열쇠도 바뀜)
    key = hashlib.md5(prompt.encode()).hexdigest()
    ans = 캐시.get("판정", key, CHOOSE_CACHE_HOURS)
    if ans is None:
        ans = _choose_vote(prompt)
        캐시.put("판정", key, ans)
    try:
        pick = int(ans.get("선택") or 0)
    except (TypeError, ValueError):
        pick = 0
    if not 1 <= pick <= len(top):
        return None
    axes = ans.get("칸") or {}
    diffs = [{"항목": AXIS_LABEL[a], "기사": axes[a].get("기사", ""), "원문": axes[a].get("원문", ""), "칸": a}
             for a in AXES if isinstance(axes.get(a), dict) and axes[a].get("판단") == "다름"]
    verdict = "표현 바뀜" if diffs else "일치"   # 판정은 코드가 칸 답으로 정한다
    return {"후보": top[pick - 1], "판정": verdict, "차이": diffs, "칸": axes,
            # PDF 직접 읽기 후보는 이미 검증한 인용(인용만)을 그대로 쓴다(머리글이 섞이지 않게)
            "원문인용": top[pick - 1].get("인용만") or ans.get("원문인용") or top[pick - 1]["원문"],
            "확인못한부분": ans.get("확인못한부분") or ""}


NEAR_SCHEMA = json_schema("near", {"선택": {"type": "integer"}, "기사표현": S, "원문표현": S, "이유": S})
NEAR_PROMPT = """기사 문장의 숫자 '{number}'와 원문 후보들이다. 앞 단계에서 '같은 지표'인 후보는 없다고 봤다.
그래도 같은 조사·같은 대상·같은 시점의 같은 숫자인데, 기사가 지표 이름이나 표현만 바꿔 쓴 후보가 있으면 그 번호를 골라라.
예: 원문 '반도체 수출액 120억 달러' → 기사 '반도체 판매 120억 달러',
    원문 '선호도 조사에서 1위 응답 41.2%' → 기사 '가장 좋아한다고 답한 비율 41%'.
대상(나라·집단·지역·품목)이나 시점(연도·월)이 다르거나, 숫자만 우연히 같은 다른 항목이면 0.
"기사표현"·"원문표현"은 바뀐 말만 짧게(원문표현은 원문 후보에 실제로 있는 말). JSON 하나로만 답하라.
기사 문장: "{article}"
원문 후보:
{candidates}"""


def near_match(sentence: str, number: str, hits: list[dict]) -> dict | None:
    """'표현 다름 후보'(11/14): 같은 숫자·같은 대상·같은 시점인데 기사가 지표 표현만 바꾼 원문 후보.
    고르기에서 '같은 지표 없음'일 때만 묻는다. 두 번 물어 같은 번호를 고를 때만 인정(우연히 같은 숫자 막기)."""
    top = hits[:TOP_K]
    lines = [f"[{i}] {h['쪽']}쪽 (원문 표기 '{h['표기']}'): \"{h['원문']}\"" for i, h in enumerate(top, start=1)]
    prompt = NEAR_PROMPT.format(number=number, article=sentence, candidates="\n".join(lines))
    key = hashlib.md5(prompt.encode()).hexdigest()
    ans = 캐시.get("표현다름", key, CHOOSE_CACHE_HOURS)
    if ans is None:
        def ask() -> dict:
            return ask_json(prompt, kind="표현 다름 후보", effort="low", routing="quality", schema=NEAR_SCHEMA)
        with ThreadPoolExecutor(2) as ex:
            a, b = list(ex.map(lambda _: ask(), range(2)))
        same = int(a.get("선택") or 0) == int(b.get("선택") or 0)
        ans = a if same else {"선택": 0, "이유": "두 번 물은 답이 달라 인정하지 않음"}
        캐시.put("표현다름", key, ans)
    try:
        pick = int(ans.get("선택") or 0)
    except (TypeError, ValueError):
        pick = 0
    if not 1 <= pick <= len(top):
        return None
    return {"후보": top[pick - 1], "기사표현": ans.get("기사표현", ""), "원문표현": ans.get("원문표현", ""),
            "이유": ans.get("이유", "")}


def _choose_vote(prompt: str) -> dict:
    """고르기 답을 다수결로(11/14 흔들림): 동시에 두 번 묻고 '선택'이 같으면 그 답, 다르면 한 번 더 물어 2표 나온 쪽.
    한 번만 물으면 같은 원문 문장을 어떤 때는 '같은 지표', 어떤 때는 '아님'이라 답했다(IEA 26%)."""
    def ask() -> dict:
        return ask_json(prompt, kind="고르기·판정", effort="low", routing="quality", schema=CHOOSE_SCHEMA)

    def pick(a: dict) -> int:
        try:
            return int(a.get("선택") or 0)
        except (TypeError, ValueError):
            return 0
    with ThreadPoolExecutor(2) as ex:
        first, second = list(ex.map(lambda _: ask(), range(2)))
    if pick(first) == pick(second):
        return first
    third = ask()
    print(f"    고르기 답이 갈려(선택 {pick(first)}·{pick(second)}) 한 번 더 물음 → {pick(third)}")
    return next(a for a in (first, second) if pick(a) == pick(third)) if pick(third) in (pick(first), pick(second)) else third


def _claimed_first(claimed, keep: list[int]) -> list[int]:
    """모델이 말한 쪽(잘라 보낸 PDF 기준·원래 PDF 기준 둘 다)을 앞에, 나머지 보낸 쪽을 뒤에."""
    order = []
    try:
        n = int(claimed)
        if 1 <= n <= len(keep):
            order.append(keep[n - 1])
        if n - 1 in keep:
            order.append(n - 1)
    except (TypeError, ValueError):
        pass
    return order + [i for i in keep if i not in order]


def verify_quote(ans: dict, pages: list[str], layout: list[str], pdf: Path, number: str,
                 keep: list[int]) -> tuple[int, str, str] | None:
    """게이트웨이 답의 인용이 PDF 에 실제로 있는지 4단계로 확인. (쪽, 확인 방법, 판정 모델에 보여 줄 원문) 또는 None.
    ① 공백 뺀 정확 일치 ② 비슷한 구간 + 그 안에 기사 숫자 ③ 숫자 뺀 글자(숫자가 그림 글자인 PDF)
    ④ 숫자만 있는 표 줄 → 줄 맞춤 글자의 같은 숫자 줄(표 제목·열 머리를 붙여 보여 줌)"""
    quote = ans.get("원문인용") or ""
    table = " ".join(x for x in [ans.get("표제목"), ans.get("행머리"), ans.get("열머리")] if x)
    # 인용 안에 기사 숫자(표기 후보 중 하나)가 실제로 있어야 한다. 없으면 모델이 스스로 계산한 것
    # (11/13: 194만원 = 405.2 − 211.2 인데 게이트웨이가 22쪽 표를 인용하며 '찾음'이라 답함 → 계산 확인으로 보냄)
    has_number = any(to_regex(v.text).search(quote) for v in variants(number))
    if (page := quote_in_pages(quote, pages)) is not None:
        return (page, "", quote) if has_number else None
    if (page := quote_fuzzy(quote, pages, number)) is not None:
        return page, " — 비슷한 구간 대조", quote
    order = _claimed_first(ans.get("쪽"), keep)
    for i in order:
        # 기본 추출은 글자 순서가 뒤섞이는 PDF 가 있어 줄 맞춤(-layout) 추출도 같이 본다
        if quote_letters_on_page(quote, pages[i]) or quote_letters_on_page(quote, page_text_layout(pdf, i + 1)):
            return i + 1, " — 숫자가 그림 글자라 숫자 뺀 문장으로 대조", quote
    for i in order:
        if has_number and i < len(layout) and (row := find_number_row(quote, layout[i])) is not None:
            lines = layout[i].split("\n")
            context = table_context(lines, row, 0, len(lines[row]))
            return i + 1, " — 표 줄 숫자 대조", f"{context} (모델이 읽은 표 정보: {table or '없음'})"
    return None


def read_pdf(pdf: Path, sentence: str, number: str, pages: list[str], keywords: list[str],
             layout: list[str] | None = None) -> dict | None:
    """정규식으로 못 찾을 때 게이트웨이 모델이 PDF 를 직접 읽게 한다.
    긴 PDF 는 핵심어가 많은 쪽만 잘라 보내고, 전체(크기 허용 시)도 동시에 보낸다(차례로 하면 2배 걸림).
    같은 PDF 를 한 번 더 보내던 것은 없앴다(11/13: 못 찾는 건이 평균 56초 걸린 주원인).
    모델 인용은 verify_quote 4단계를 통과해야 인정."""
    whole = list(range(len(pages)))
    whole_ok = len(pages) <= WHOLE_SEND_MAX_PAGES and pdf.stat().st_size <= PDF_MAX_BYTES
    attempts: list[tuple[list[int], Path]] = []   # (보낸 쪽 번호들, 보낼 PDF)
    if len(pages) > SEND_PAGES + HEAD_PAGES:
        picked = pick_pages(pages, keywords)
        few_hits = len(picked) <= HEAD_PAGES + MIN_KEYWORD_PAGES
        if not (few_hits and whole_ok):
            print(f"    {len(pages)}쪽 중 {len(picked)}쪽만 잘라 보냄: {[i + 1 for i in picked]}")
            attempts.append((picked, make_sub_pdf(pdf, picked)))
        if whole_ok:
            print(f"    PDF 전체({len(pages)}쪽)도 동시에 보냄")
            attempts.append((whole, pdf))
    else:
        attempts.append((whole, pdf))
    prompt = PDF_FIND_PROMPT.format(article=sentence, number=number)

    def one(job: tuple[list[int], Path]) -> dict:
        try:
            return ask_json(prompt, pdf_path=job[1], kind="PDF 직접 읽기", effort="low", schema=PDF_FIND_SCHEMA)
        except RuntimeError as e:
            print(f"    PDF 직접 읽기 실패: {e}")
            return {}

    with ThreadPoolExecutor(len(attempts)) as ex:
        answers = list(ex.map(one, attempts))
    for (keep, _), ans in zip(attempts, answers):   # 일부 쪽 답 → 전체 답 순서로, 검증 통과한 첫 답
        if not ans.get("찾음"):
            print(f"    게이트웨이 모델: {len(keep)}쪽 읽기에서 못 찾음")
            continue
        checked = verify_quote(ans, pages, layout or [], pdf, number, keep)
        if checked is None:
            print(f"    PDF 직접 읽기 인용이 원문에 없거나 기사 숫자가 없어 버림: {(ans.get('원문인용') or '')[:60]}")
            continue
        page, how, shown = checked
        # 표 줄만으로는 무슨 지표인지 모르므로, 판정 모델에 그 쪽 머리글(표 제목·단위)도 같이 보여 준다
        head = pages[page - 1][:PAGE_HEAD_CHARS] if page <= len(pages) else ""
        return {"쪽": page, "표기": number, "종류": "PDF 직접 읽기(라이너 게이트웨이)" + how, "핵심어": [],
                "점수": 0, "단위표시": "", "원문": f"(이 쪽 머리글: {head} …) {shown}",
                "인용만": ans.get("원문인용") or ""}
    return None


def mentions_raw_data(sentence: str) -> bool:
    """기사 문장이 보고서가 아니라 통계표(KOSIS)·마이크로데이터를 인용했다고 밝히는지.
    (기사 문맥 전체로 넓히면 다른 문장의 'KOSIS'에 걸려 오판하므로 문장만 본다)"""
    return 출처종류.classify(sentence) == "원자료 분석"
