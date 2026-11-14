"""숫자 표기 맞추기 오프라인 테스트(라이너 API 없이 몇 초 — 11/15).

기사 숫자 표기와 원문 표기가 달라도 같은 값이면 맞추고, 다른 값이면 맞추지 않는지 확인한다.
사례마다 (기사 숫자, 원문 글, 맞아야 하는지). 실제로 겪은 사례는 끝에 날짜·출처를 적었다.
사용: python3 도구/테스트_숫자표기.py   (pytest 가 있으면 python3 -m pytest 도구/테스트_숫자표기.py 도 됨)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from 기사정리 import is_condition_number  # noqa: E402
from 숫자표기 import to_regex, variants  # noqa: E402

CASES = [
    # (기사 숫자, 원문 글, 맞아야 함)
    ("43%", "응답자의 43.0%가 그렇다고 답했다", True),
    ("3.6%", "전년 대비 3.62% 증가", True),                       # 원문이 더 긴 소수(반올림)
    ("3.6%", "전년 대비 3.66% 증가", False),                      # 반올림하면 3.7
    ("53%", "미용(뷰티, 52.6%)", True),                            # 11/14 해외한류실태조사 올림
    ("55%", "음식(55.1%)", True),
    ("55%", "음식(56.1%)", False),
    ("약 1.13%포인트", "a 113-basis-point increase for the last month", True),   # 11/14 NBER
    ("약 1.13%포인트", "rose 1.1 percentage points", False),                       # 11/15 소수 자르기 버그
    ("0.7%p", "0.7%포인트 하락", True),
    ("1784원", "리터당 1,784원", True),                            # 11/14 석유 최고가격 고시(쉼표)
    ("1,465건", "1465건을 분석", True),
    ("1조 6500억 원", "내년 1조 6,500억 원으로", True),            # 11/14 경기도(단위 속 쉼표)
    ("19억 7100만 원", "19 억 7,100 만 원이 적발", True),          # 11/14 강남구
    ("867만 1천명", "867만 1천 명", True),
    ("10만3000명", "10만 3천명", True),
    ("1840만배럴", "Gulf oil exports reached 18.4 mb/d", True),    # 11/14 IEA million
    ("14조94억원", "매출 14,009,399백만원이며", True),             # 11/14 LG이노텍 백만원 단위
    ("1352억원", "135,195,115,866〔환산 1352억원·1351억원〕", True),   # DART 환산 표기
    ("4490만 달러", "License revenue $ in millions 44.9", True),   # 11/14 넷리스트(표 머리 필요한 변형)
    ("약 1억2000만명", "延べ約1.2億人の利用があった", True),        # 11/14 일본 임야청
    ("3명", "선임할 이사의 수: 3인(사내이사 1인", True),            # 11/14 오스코텍(단위 바꿈)
    ("3명", "2026년 3월 13일", False),                              # 한 자리 맨숫자는 맞추지 않음
    ("1", "제1장 개요", False),
    ("22건", "We included 22 studies in the meta-analysis", True),
    ("9천151억원", "9,151억원", True),
    ("6.5%", "increased 6.5% for the 12 months", True),
    ("6.5%", "increased 16.5% for the 12 months", False),          # 앞 숫자에 붙은 것은 다른 값
]

CONDITIONS = [
    # (문장, 숫자, 기준값인지)
    ("노인 3명 중 1명 이상은 중위소득의 50%에도 못 미치는 상대적 빈곤 상태", "50%", True),   # 11/15 헛답 원인
    ("200달러 이하 소액 헌금 비중은 65%에 달한다", "200달러", True),
    ("200달러 이하 소액 헌금 비중은 65%에 달한다", "65%", False),
    ("인구 10만 명당 27.8명", "10만 명", True),
    ("전년 대비 3.0% 증가했다", "3.0%", False),
]


def matches(number: str, text: str) -> bool:
    """unit 이 필요한 변형(need_unit)은 그 글에 단위 표시가 있을 때만(PDF도구.find_candidates 와 같은 규칙)."""
    from PDF도구 import unit_ok
    return any(to_regex(v.text).search(text) and unit_ok(v, text) for v in variants(number))


def test_variants() -> None:
    bad = [(n, t, want) for n, t, want in CASES if matches(n, t) != want]
    assert not bad, "표기 맞추기 실패:\n" + "\n".join(f"  기사 '{n}' ↔ 원문 '{t}' : 기대 {w}" for n, t, w in bad)


def test_conditions() -> None:
    bad = [(s, n, want) for s, n, want in CONDITIONS if is_condition_number(s, n) != want]
    assert not bad, "기준값 판별 실패:\n" + "\n".join(f"  '{n}' in '{s}' : 기대 {w}" for s, n, w in bad)


if __name__ == "__main__":
    failed = 0
    for name, fn in [("숫자 표기", test_variants), ("기준값 판별", test_conditions)]:
        try:
            fn()
            print(f"통과: {name}")
        except AssertionError as e:
            failed += 1
            print(f"실패: {name}\n{e}")
    print(f"\n사례 {len(CASES) + len(CONDITIONS)}개 — {'모두 통과' if not failed else f'{failed}개 묶음 실패'}")
    sys.exit(1 if failed else 0)
