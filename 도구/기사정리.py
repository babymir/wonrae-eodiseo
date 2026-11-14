"""라이너 모델이 나눈 기사 정보 중 코드로 정할 수 있는 것을 코드가 정한다(실행마다 흔들리지 않게).

라이너 API 는 temperature 를 받지 않아 같은 문장도 '보고서'·'기준시점' 칸이 실행마다 달라진다.
- 기사 따옴표 속 보고서 제목이 있으면 그대로 쓴다
- '9월'·'3분기'처럼 연도 없는 기준 시점에 기사 날짜로 연도를 채운다
- 문장 나누기를 동시에 두 번 하고, 기사 글자에 실제로 있는 쪽을 고른다(extract_twice)
"""
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor

from 숫자표기 import core_number
import 캐시
from 판정 import EXTRACT_PROMPT, extract
from 후보문서 import article_day

QUOTED_TITLE = re.compile(r"[‘'「『“\"]([^’'」』”\"]{6,80})[’'」』”\"]")


TITLE_HINT = re.compile(r"보고서|조사|통계|동향|현황|과제|방안|분석|연구|포커스|포럼|이슈|브리프|결과|실태")


def _with_year(period: str, article_date: str) -> str:
    """'9월'·'3분기'처럼 연도 없는 기준 시점에 기사 날짜로 연도를 채운다.
    기사 달보다 뒤의 달·분기면 작년(1월 기사의 '12월' → 작년 12월).
    (11/13 시험: '9월'만으로 2024년 9월호를 '시점 맞는 판'으로 골라 다른 해 숫자를 근거로 댐)"""
    day = article_day(article_date)
    if not period or not day or re.search(r"20\d{2}", period):
        return period
    if m := re.search(r"(\d{1,2})\s*월", period):
        month = int(m.group(1))
    elif q := re.search(r"(\d)\s*(?:/4)?\s*분기", period):
        month = int(q.group(1)) * 3
    else:
        return period
    year = day.year if month <= day.month else day.year - 1
    return f"{year}년 {period.strip()}"


def stabilize(info: dict, sentence: str, article_date: str = "") -> dict:
    """모델 분해 결과 중 코드로 정할 수 있는 것은 코드가 정한다(라이너 API 는 temperature 를 받지 않아
    같은 문장도 실행마다 '보고서' 칸이 달라지고, 그 때문에 검색·후보가 흔들림 — 11/13 새 기사 #16).
    기사에 따옴표로 묶인 보고서 이름이 있으면 그것을 그대로 쓴다."""
    period = _with_year(info.get("기준시점", ""), article_date)
    if period != info.get("기준시점", ""):
        print(f"    기준 시점에 기사 날짜로 연도를 채움: '{info.get('기준시점')}' → '{period}'")
        info = {**info, "기준시점": period}
    quoted = []
    for m in QUOTED_TITLE.finditer(sentence):
        q, after = m.group(1).strip(), sentence[m.end(): m.end() + 8]
        if re.fullmatch(r"[\d\s.,%]+", q):
            continue
        if TITLE_HINT.search(q) or re.match(r"\s*(?:보고서|자료|이슈|브리프|에\s*따르면|를\s*보면|에\s*의하면)", after):
            quoted.append(q)
    if quoted:
        best = re.sub(r"(\d)\s+(월|년|분기)", r"\1\2", max(quoted, key=len))
        if info.get("보고서") != best:
            print(f"    보고서 이름을 기사 따옴표 속 제목으로 고정: '{best}' (모델: '{info.get('보고서', '')}')")
        info = {**info, "보고서": best}
    return info


def _squash(text: str) -> str:
    return re.sub(r"\s", "", text or "")


def _merge(a: dict, b: dict, sentence: str, context: str) -> dict:
    """두 번 나눈 결과를 합친다: 보고서·숫자는 기사 글자에 실제로 있는 쪽, 핵심어는 합침, 기준시점은 연도가 있는 쪽."""
    text = _squash(sentence + context)

    def in_text(value: str) -> bool:
        return bool(value) and _squash(value) in text

    def pick(key: str, check, shorter: bool = False) -> str:
        va, vb = a.get(key) or "", b.get(key) or ""
        if check(va) != check(vb):
            return va if check(va) else vb
        # 둘 다 맞거나 둘 다 아니면: 이름·시점은 더 자세한 쪽, 숫자는 군말 없는 짧은 쪽
        return (min if shorter else max)(va, vb, key=len) or va or vb

    keywords = list(dict.fromkeys([*a.get("핵심어", []), *b.get("핵심어", [])]))[:5]
    return {**a,
            "기관": a.get("기관") or b.get("기관") or "",
            "보고서": pick("보고서", in_text),
            "숫자": pick("숫자", lambda v: bool(v) and core_number(v).rstrip("%~") in _squash(sentence), shorter=True),
            "핵심어": keywords,
            "기준시점": pick("기준시점", lambda v: bool(re.search(r"20\d{2}", v or ""))),
            "기관누리집": a.get("기관누리집") or b.get("기관누리집") or "",
            "원어검색어": max(a.get("원어검색어") or "", b.get("원어검색어") or "", key=len)}


EXTRACT_CACHE_HOURS = 24 * 30   # 같은 문장(문맥·날짜·지시문 판)이면 한 달 동안 같은 분해를 쓴다


def extract_cached(sentence: str, context: str, article_date: str) -> dict:
    """같은 입력에는 같은 분해(11/14): 라이너 API 는 temperature 를 받지 않아 분해가 조금씩 달라지고,
    그 차이가 검색어 → 받는 후보 → 판정까지 번져 같은 문장의 결과가 실행마다 바뀌었다(IEA·FEC·청년 은둔).
    열쇠에 지시문(EXTRACT_PROMPT)을 넣어, 지시문을 고치면 새로 분해한다."""
    key = json.dumps([sentence, context, article_date, hashlib.md5(EXTRACT_PROMPT.encode()).hexdigest()],
                     ensure_ascii=False)
    if (hit := 캐시.get("분해", key, EXTRACT_CACHE_HOURS)) is not None:
        return hit
    info = extract_twice(sentence, context, article_date)
    캐시.put("분해", key, info)
    return info


def extract_twice(sentence: str, context: str, article_date: str) -> dict:
    """문장 나누기를 동시에 두 번 해서 합친다(라이너 API 는 temperature 를 받지 않아 한 번에 기대면 흔들림).
    한쪽만 성공하면 그쪽을, 둘 다 실패하면 첫 오류를 그대로 낸다."""
    with ThreadPoolExecutor(2) as ex:
        futures = [ex.submit(extract, sentence, context, article_date) for _ in range(2)]
    results, errors = [], []
    for f in futures:
        try:
            results.append(f.result())
        except RuntimeError as e:
            errors.append(e)
    if not results:
        raise errors[0]
    return _merge(results[0], results[-1], sentence, context) if len(results) == 2 else results[0]


# 문장 속 '확인할 만한 숫자'(단위가 붙은 값). 연·월·일·시각·나이대는 뺀다(A4, 11/14).
NUMBER_WITH_UNIT = re.compile(
    r"(?<![\d.,])[△▲▼-]?\d[\d,.]*\s*(?:만\s*\d[\d,]*\s*)?(?:천\s*)?"
    r"(?:%포인트|%p|%|퍼센트|조\s*\d[\d,]*\s*억\s*원|조\s*원|억\s*\d[\d,]*\s*만\s*원|억\s*원|만\s*원|천\s*원|원|"
    r"달러|위안|엔|유로|명|건|곳|개사|개|톤|t|배럴|가구|배|㎞|km|bp|포인트|㎡|㏊|대|척)")
NOT_VALUE = re.compile(r"^\d+\s*대$")   # '60대'(나이대) 같은 것


# 기준값·조건 숫자: '중위소득의 50%에도 못 미치는', '200달러 이하', '3명 중', '10만 명당' — 주장이 아니라 기준이다
# (11/15: 노인 빈곤 문장에서 기준값 50%를 골라 '상대적 빈곤율' 정의 문장과 맞춰 '표현 바뀜' 헛답)
CONDITION_AFTER = r"\s*(?:의|을|를|에|에도|도)?\s*(?:못\s*미치|미달|미만|이하|이상|초과|넘는|넘은|넘어|웃도|밑도|기준|중\b|중\s|당|까지|부터)"


def is_condition_number(sentence: str, number: str) -> bool:
    """문장 속 그 숫자가 기준값·조건(이하·미만·못 미치는·중·당 …)으로 쓰였는지."""
    if re.search(r"(?:이상|이하|미만|초과|넘는|미달)\s*$", number.strip()):
        return True   # 분해한 숫자 자체에 기준 표현이 붙음('0.08% 이상' — 11/16 신규 세트2 음주 기준 헛답)
    core = re.escape(number.strip())
    return bool(re.search(core.replace(r"\ ", r"\s*") + CONDITION_AFTER, sentence))


def all_numbers(sentence: str) -> list[str]:
    """문장 속 단위 붙은 숫자 전부(나온 순서, 중복 없음)."""
    out = []
    for m in NUMBER_WITH_UNIT.finditer(sentence):
        v = m.group(0).strip()
        if not NOT_VALUE.match(v) and v not in out:
            out.append(v)
    return out
