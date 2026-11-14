"""코드로 확실히 가를 수 있는 '달라진 점'을 규칙으로 확인한다(모델 판정의 보조·견제).

모델이 '일치'라고 해도 규칙이 다름을 찾으면 '표현 바뀜'으로 내리고 근거를 남긴다.
확인 항목(숫자 바로 앞뒤 RULE_WINDOW 글자만 본다):
- 단위: 기사 %  ↔ 원문 %p(%포인트),  기사 배 ↔ 원문 %
- 방향: 기사 '증가' ↔ 원문 '감소'처럼 반대 (일치 금지)
- 기간: 비교 기준(전년동월 대비·전월 대비·전년 대비·전분기 대비)이 서로 다름
모집단·지표 정의처럼 규칙으로 못 가르는 것은 모델에 맡긴다.
"""
import re

from 숫자표기 import to_regex

RULE_WINDOW = 25
UP = re.compile(r"증가|상승|늘|올라|올랐|확대|높아|높았|개선|플러스|많아|많았")
DOWN = re.compile(r"감소|하락|줄|내려|내렸|축소|낮아|낮았|악화|마이너스|적어|적었")
PERIODS = {"전년동월": r"전년\s*동월|1년\s*전\s*같은\s*달|(?:지난해|작년)\s*같은\s*달",
           "전월": r"전월|전달|한\s*달\s*전",
           "전년동기": r"전년\s*동기|(?:지난해|작년)\s*(?:같은\s*기간|동기)",
           "전분기": r"전분기|직전\s*분기",
           "전년": r"전년(?!\s*동)|1년\s*전(?!\s*같은)|(?:지난해|작년)(?!\s*(?:같은|동))"}

# 같은 비교 기준끼리 묶음: '1년 전보다'·'전년'은 월간 통계면 전년동월, 분기 통계면 전년동기와 같은 뜻
# (11/13 기존 #12: '1년 전보다' ↔ '전년동월대비'를 기간 다름으로 오판)
PERIOD_FAMILY = {"전년동월": "1년 전", "전년동기": "1년 전", "전년": "1년 전", "전월": "직전 달", "전분기": "직전 분기"}


def _pattern(mark: str) -> re.Pattern:
    """기사 숫자·원문 표기 그대로(표기 후보로 넓히면 '3' 같은 짧은 숫자가 엉뚱한 곳에 걸림)."""
    return to_regex(re.sub(r"\s", "", mark).rstrip("명원건개곳가구배%p포인트") or mark)


def _windows(text: str, mark: str) -> list[str]:
    """text 안에서 mark 가 나온 곳 앞뒤 RULE_WINDOW 글자들."""
    return [text[max(0, m.start() - RULE_WINDOW): m.end() + RULE_WINDOW] for m in _pattern(mark).finditer(text)]


def _unit_after(text: str, number: str) -> str:
    """숫자 바로 뒤 단위 표시: '%p' / '%' / '배' / ''."""
    for m in _pattern(number).finditer(text):
        if m:
            tail = text[m.end(): m.end() + 6].replace(" ", "")
            if tail.startswith(("%p", "%포인트", "퍼센트포인트", "포인트")):
                return "%p"
            if tail.startswith(("%", "퍼센트")):
                return "%"
            if tail.startswith("배"):
                return "배"
    return ""


def _direction(window: str) -> str:
    up, down = bool(UP.search(window)), bool(DOWN.search(window))
    return "증가" if up and not down else "감소" if down and not up else ""


def _periods(window: str) -> set[str]:
    return {name for name, pat in PERIODS.items() if re.search(pat, window)}


def check(article: str, number: str, quote: str, quote_mark: str) -> list[dict]:
    """기사 문장·숫자와 원문 인용·원문 표기를 비교해 규칙으로 찾은 차이 목록.
    각 항목: {"항목", "기사", "원문", "규칙"}. 원문에서 숫자를 못 찾으면(표 줄 등) 확인 가능한 것만."""
    diffs = []
    a_unit, q_unit = _unit_after(article, number), _unit_after(quote, quote_mark or number)
    if {a_unit, q_unit} == {"%", "%p"}:
        diffs.append({"항목": "단위", "기사": a_unit, "원문": q_unit, "규칙": "%와 %p(퍼센트포인트)는 다른 단위"})
    if {a_unit, q_unit} == {"%", "배"}:
        diffs.append({"항목": "단위", "기사": a_unit, "원문": q_unit, "규칙": "배와 %는 다른 표현(2배 = 100% 증가)"})
    a_win = " ".join(_windows(article, number))
    q_win = " ".join(_windows(quote, quote_mark or number))
    if a_win and q_win:
        a_dir, q_dir = _direction(a_win), _direction(q_win)
        if a_dir and q_dir and a_dir != q_dir:
            diffs.append({"항목": "용어", "기사": a_dir, "원문": q_dir, "규칙": "증감 방향이 반대"})
        a_per, q_per = _periods(a_win), _periods(q_win)
        a_fam, q_fam = {PERIOD_FAMILY[x] for x in a_per}, {PERIOD_FAMILY[x] for x in q_per}
        if a_fam and q_fam and not (a_fam & q_fam):
            diffs.append({"항목": "기간", "기사": "·".join(sorted(a_per)) + " 대비",
                          "원문": "·".join(sorted(q_per)) + " 대비", "규칙": "비교 기준 기간이 다름"})
    return diffs


if __name__ == "__main__":  # 간단 확인
    print(check("실업률이 1.3% 증가했다", "1.3%", "실업률은 1.3%p 상승", "1.3"))
    print(check("취업자는 전월 대비 3만명 줄었다", "3만명", "취업자는 전년동월대비 3만명 증가", "3만"))
    print(check("물가는 전년동월 대비 2.1% 올랐다", "2.1%", "소비자물가는 전년동월대비 2.1% 상승", "2.1%"))
    print(check("거래액은 24조2118억원으로 1년 전보다 7.3% 늘었다", "7.3%", "24조 2,118억원으로 전년동월대비 7.3% 증가", "7.3%"))
    print(check("판매는 작년 동기보다 0.5% 감소", "0.5%", "전분기대비 0.5% 감소", "0.5%"))
