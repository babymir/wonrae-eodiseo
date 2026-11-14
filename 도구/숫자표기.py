"""기사 숫자 → 원문에서 찾을 표기 후보 여러 개.

예) '867만 1천명' → 867만1천, 867.1만, 8,671,000, 8,671(표, 천 명 단위)
    '43%'        → 43%, 43.0%
원문 표기 그대로면 '정확', 단위를 바꾼 표기면 '표기변환'으로 구분한다.
"""
import re
from dataclasses import dataclass

UNIT_VALUES = {"조": 10**12, "억": 10**8, "만": 10**4, "천": 10**3}
NUM_UNIT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(조|억|만|천)?")


@dataclass(frozen=True)
class Variant:
    text: str   # 찾을 표기
    kind: str   # '정확' 또는 '표기변환(설명)'
    need_unit: str = ""  # 이 표기는 그 쪽에 단위 표시(예: '%')가 있을 때만 인정(표 안 맨 숫자)


def _trim(x: float) -> str:
    """1.0 → '1', 867.10 → '867.1'"""
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return s


def _korean_value(number: str) -> int | None:
    """'867만 1천명' → 8671000, '9천151억' → 915100000000. 조·억·만·천이 없으면 None.
    조·억·만은 자리 묶음을 끝내고, 천은 그 묶음 안의 자리다(11/14: '9천151억'을 1조5100억으로 읽던 버그)."""
    text = re.sub(r"\s", "", number)
    if not re.search(r"[조억만천]", text):
        return None
    total, group, current = 0.0, 0.0, None
    for num, unit in re.findall(r"(\d[\d,]*(?:\.\d+)?)|(조|억|만|천)", text):
        if num:
            current = float(num.replace(",", ""))
        elif unit == "천":
            group += (current if current is not None else 1) * 10**3
            current = None
        else:
            group += current or 0
            total += (group or 1) * UNIT_VALUES[unit]
            group, current = 0.0, None
    total += group + (current or 0)
    return int(round(total))


# 모델이 '숫자' 칸에 앞뒤 말까지 넣는 경우가 있음('인구 10만 명당 27.8명', '2024년 6.0%', '19% 감소')
# → 그대로 쓰면 '100,028' 같은 엉뚱한 표기가 되어 원문에서 못 찾음(11/13 새 기사 #8·15·16·18)
NUMBER_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?(?:(?:조|억|만|천)(?:\d[\d,]*(?:\.\d+)?)?)*"
                          r"(?:%p|%포인트|%|명|원|달러|건|개|곳|가구|배)?")
NOT_AMOUNT_AFTER = re.compile(r"(년|월|분기|반기|세|개월|일|호|차)")


def core_number(number: str) -> str:
    """'인구 10만 명당 27.8명' → '27.8명'. 범위('~')는 그대로 둔다.
    연도·월 같은 시점 숫자는 빼고, % 가 붙은 것을 먼저, 없으면 마지막 숫자를 고른다."""
    raw = re.sub(r"\s", "", number).replace("∼", "~").replace("～", "~")
    if "~" in raw:
        # 범위 '1,722조~2,471조원'·'20~30%': 앞 숫자로 찾는다(뒤에만 단위가 있으면 붙여 줌).
        # 11/13 기존 #2: 범위 그대로 찾아 원문 '1,722조원'을 못 맞춤
        first, second = raw.split("~", 1)
        unit = re.sub(r"^[\d,.]+", "", second)
        if first and not re.search(r"[^\d,.]", first):
            first += unit
        raw = first or raw
    tokens = [m.group(0) for m in NUMBER_TOKEN.finditer(raw)
              if not NOT_AMOUNT_AFTER.match(raw, m.end())]
    if not tokens:
        return raw
    pct = [t for t in tokens if "%" in t]
    return pct[-1] if pct else tokens[-1]


def variants(number: str) -> list[Variant]:
    raw = core_number(number)
    core = re.sub(r"(명분|명|원|달러|건|개|곳|가구|배|t|톤)$", "", raw)
    if re.fullmatch(r"\d", core):
        # 한 자리 맨숫자('3명 중 1명'의 1)는 원문 아무 데나 있다 → 맨숫자로는 대조하지 않는다(11/14: 엉뚱한 '1'과 맞춰 '일치').
        # 단위가 붙은 표기('3명'·'3인'·'3곳')로만 찾는다(11/14 무작위2: 오스코텍 공시 '선임할 이사의 수: 3인')
        unit = raw[len(core):]
        if not unit:
            return []
        alts = [core + unit] + ([core + "인"] if unit == "명" else []) + ([core + "개사"] if unit == "곳" else [])
        return [Variant(a, "정확" if a == core + unit else "표기변환(단위 바꿈)") for a in alts]
    found: list[Variant] = [Variant(core, "정확")]
    # 쉼표 있고 없음: 기사 '1784원' ↔ 원문 '1,784원'(11/14 석유 최고가격 고시), 기사 '1,465건' ↔ 원문 '1465건'
    if re.fullmatch(r"\d{4,}", core):
        found.append(Variant(f"{int(core):,}", "표기변환(쉼표)"))
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})+", core):
        found.append(Variant(core.replace(",", ""), "표기변환(쉼표)"))

    # 퍼센트: 43% ↔ 43.0%,  %p ↔ %포인트
    pm = re.fullmatch(r"(\d+(?:\.\d+)?)(%p|%포인트|%)", core)
    if pm:
        num, sign = pm.groups()
        signs = ["%p", "%포인트"] if sign != "%" else ["%"]
        # 43% ↔ 43.0% 만(소수가 있는 값을 소수 한 자리로 자르지 않는다 — 11/15: '1.13%p'에서 '1.1%p'를 만들던 버그)
        nums = {num} | ({num + ".0"} if "." not in num else set())
        for n in sorted(nums):
            for s in signs:
                text = n + s
                if text != core:
                    found.append(Variant(text, "표기변환(소수점·기호)"))
        # 베이시스포인트: 기사 '1.13%포인트' ↔ 원문 '113-basis-point'·'113 bp'(11/14 NBER 논문)
        if sign != "%":
            bp = round(float(num) * 100)
            if bp >= 1:
                found.append(Variant(rf"re:{bp}(?=[\s-]*(?:basis[\s-]*points?|bps?\b))", "표기변환(베이시스포인트)"))
        # 반올림: 기사 91% ↔ 원문 91.17%, 기사 3.6% ↔ 원문 3.62% (11/13 새 기사 #5)
        if sign == "%":
            found.append(Variant((num if "." in num else num + ".") + "~", "표기변환(반올림, 원문은 더 긴 소수)"))
            if "." not in num and int(num) >= 1:
                # 올림: 기사 53% ↔ 원문 52.6% (11/14 무작위3 해외한류실태조사 55.1%·52.6% → 55%·53%)
                found.append(Variant(rf"re:{int(num) - 1}\.[5-9]\d*(?=\s*%)", "표기변환(반올림, 원문은 더 긴 소수)"))
        # 표 안에는 '%' 없이 맨 숫자로 적힘("(단위: %, %p)" 표). 소수가 있는 값만(정수는 오탐 많음)
        for n in sorted(nums):
            if "." in n:
                found.append(Variant(n, "표기변환(표 숫자, 단위 %)", need_unit="%"))
        return found

    value = _korean_value(core)
    if value is None:
        # 소수: 기사 2.18 ↔ 원문 2.1821 (원문을 반올림한 값). 원문 뒷자리는 to_regex 에서 허용
        if re.fullmatch(r"\d+\.\d+", core):
            found.append(Variant(core + "~", "표기변환(반올림, 원문은 더 긴 소수)"))
        return found
    found.append(Variant(f"{value:,}", "표기변환(전체 자릿수)"))
    for unit, size in (("만", 10**4), ("억", 10**8), ("조", 10**12)):
        if size <= value < size * 10**4:  # '92440000만' 같은 어색한 표기는 빼기
            found.append(Variant(_trim(value / size) + unit, f"표기변환({unit} 단위 소수)"))
    # 만+천 표기: 기사 '23만2000가구' ↔ 원문 '23만 2천가구'(11/13 처음 보는 기사 #9)
    if 10**4 <= value < 10**8 and value % 10**3 == 0:
        man, rest = divmod(value, 10**4)
        found.append(Variant(f"{man}만" + (f"{rest // 10**3}천" if rest else ""), "표기변환(만·천 표기)"))
    # 영문 원문의 million·billion: '1840만배럴' ↔ '18.4 mb/d'·'18.4 million'(11/14 IEA), '4조52억 달러' ↔ '4.0052 trillion'
    for word, size in (("m", 10**6), ("b", 10**9), ("t", 10**12)):
        if size <= value < size * 1000:
            found.append(Variant(f"{_trim(value / size)} {word}", f"표기변환(영문 {word}illion)"))
    # 한글 단위 속 쉼표: 기사 '1조 6500억' ↔ 원문 '1조 6,500억'(11/14 경기도 보도자료), 반대도
    for v in list(found):
        if re.search(r"[조억만]", v.text) and not v.text.startswith("re:"):
            commas = re.sub(r"(?<![\d,.])(\d)(\d{3})(?=[조억만천])", r"\1,\2", v.text)
            plain = re.sub(r"(?<=\d),(?=\d{3}[조억만천])", "", v.text)
            for alt in (commas, plain):
                if alt != v.text:
                    found.append(Variant(alt, "표기변환(단위 속 쉼표)"))
    # 영문 원문의 개수 표기: '22건' ↔ '22 studies', '1006명' ↔ '1,006 respondents'(11/14 메타분석 논문)
    eng = {"건": "studies|trials|cases|articles|papers|reports", "편": "studies|articles|papers",
           "명": "people|persons|participants|respondents|patients|adults|women|men|individuals|workers|students",
           "곳": "companies|firms|sites|facilities|institutions", "개": "countries|items|companies"}
    unit_word = re.search(r"(건|편|명|곳|개)$", raw)
    if unit_word and value is not None and value >= 2:
        nums = "|".join(re.escape(x) for x in dict.fromkeys([str(value), f"{value:,}"]))
        found.append(Variant(rf"re:(?:{nums})(?=\s+(?:[a-z-]+\s+)?(?:{eng[unit_word.group(1)]})\b)",
                             "표기변환(영문 개수 표기)"))
    # 일본·중국 원문의 億·万: '1.2억 명' ↔ '約1.2億人'(11/14 일본 임야청 백서)
    for v in list(found):
        if re.search(r"[억만]", v.text) and not v.text.startswith("re:"):
            found.append(Variant(v.text.replace("억", "億").replace("만", "万"), "표기변환(한자 億·万)"))
    # 영문 재무표의 백만·십억 달러 단위 맨 숫자('4490만 달러' ↔ 'License revenue 44.9', 표 머리 '$ in millions')
    if re.search(r"달러|USD|\$", raw):
        if 10**6 <= value < 10**12:
            found.append(Variant(_trim(value / 10**6), "표기변환(표 숫자, 단위 백만 달러)", need_unit="million"))
        if 10**9 <= value < 10**15:
            found.append(Variant(_trim(value / 10**9), "표기변환(표 숫자, 단위 십억 달러)", need_unit="billion"))
    # 공시·재무표의 백만원 단위: 기사가 억 단위로 반올림한 값이면 원문은 ±50백만 안의 값
    # (11/14: LG이노텍 '14조94억원' ↔ 분기보고서 '14,009,399백만원')
    if value >= 10**10 and value % 10**8 == 0:
        center = value // 10**6
        alts = "|".join(re.escape(f"{m:,}") for m in range(center - 50, center + 50))
        found.append(Variant("re:" + alts, "표기변환(표 숫자, 단위 백만, 억 반올림)"))
    elif value >= 10**6 and value % 10**6 == 0:
        found.append(Variant(f"{value // 10**6:,}", "표기변환(표 숫자, 단위 백만)"))
    # 표 안 숫자: 천·만·억 단위로 나눈 값 (단위는 표 머리에 있음)
    for label, size in (("천", 10**3), ("만", 10**4), ("억", 10**8)):
        if value >= size and value % size == 0:
            found.append(Variant(f"{value // size:,}", f"표기변환(표 숫자, 단위 {label})"))
    # 중복 제거(앞의 것 우선)
    seen, unique = set(), []
    for v in found:
        if v.text not in seen:
            seen.add(v.text)
            unique.append(v)
    return unique


def to_regex(text: str) -> re.Pattern:
    """글자 사이 공백을 허용하고, 앞뒤가 다른 숫자에 붙지 않게 한다.
    끝이 '~'인 반올림 표기는 뒤에 숫자가 더 붙고 반올림하면 같은 값만 맞춘다(lookahead 로 근사 확인)."""
    if text.startswith("re:"):   # 여러 값 중 하나(반올림 범위)
        return re.compile(rf"(?<![\d.,])(?:{text[3:]})(?![\d]|[.,]\d)")
    if text.endswith("~"):
        base = text[:-1]
        body = re.escape(base)
        # 다음 자리가 0~4면 내림(그대로), 5~9 는 올림이라 다른 값 → 0~4만 허용
        return re.compile(rf"(?<![\d.,]){body}[0-4]\d*(?!\d)")
    body = r"\s*".join(re.escape(ch) for ch in text)
    return re.compile(rf"(?<![\d.,]){body}(?![\d]|[.,]\d)")


if __name__ == "__main__":  # 간단 확인
    for n in ["867만 1천명", "3.6%", "43%", "10만3000명", "9244억 달러", "194만원", "0.7%p",
              "인구 10만 명당 27.8명", "2024년 6.0%", "19% 감소", "41만 6천 명"]:
        print(n, "→", [(v.text, v.kind) for v in variants(n)])
