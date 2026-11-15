"""헛답 막기 규칙 오프라인 테스트(라이너 API 없이 몇 초 — 11/16).

'원문 확인'으로 잘못 내던 실제 사례(헛답)는 막고, 맞게 찾은 사례는 그대로 두는지 확인한다.
규칙을 고치다 다른 사례가 깨지면 여기서 먼저 드러난다. 사례 끝에 날짜·출처를 적었다.
사용: python3 도구/테스트_헛답막기.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import 일반경로  # noqa: E402
from 원문찾기 import _guard_condition, _guard_news, _guard_other_org, _guard_year  # noqa: E402
from 입구 import only_method_numbers  # noqa: E402

KEPT, DROPPED = "그대로", "원출처 미확인"


def found(sentence: str, quote: str, **extra) -> dict:
    """'원문 확인' 판정 결과 한 건(테스트용 최소 모양)."""
    return {"입력": sentence, "판정": "일치", "보고서": "테스트 문서", "상세페이지": "https://example.go.kr/a",
            "원문인용": quote, "추출": {"기준시점": "2026년 11월"}, **extra}


def outcome(result: dict, before: str = "일치") -> str:
    return DROPPED if result.get("판정") == "원출처 미확인" and before != "원출처 미확인" else KEPT


# (이름, 결과, 기사 날짜, 기대)
YEAR_CASES = [
    ("옛 목표주가 표 줄(11/16 무작위 #5)",
     found("한국투자증권은 신세계의 목표주가를 53만원에서 41만원으로 하향했다.", "2011.07.14 매수 410,000 원"),
     "2026-11-14", DROPPED),
    ("논문의 자료 기간(연도만) — 그대로(신규세트 #39)",
     found("흡연자의 치매 발생 위험이 1.3배 높았다.", "Dementia incidence during 2013–2019, smoking status in 2012"),
     "2026-11-10", KEPT),
    ("DART 표 숫자 속 자릿수 — 그대로(검증세트 #6)",
     found("영업이익은 271억원이었다.", "영업이익 | 제 51 기 3분기 3개월: 27,054,566,087〔환산 271억원〕"),
     "2026-11-14", KEPT),
    ("작년 날짜(1년 차) — 그대로",
     found("지난해 10월 수출은 5.1% 늘었다.", "2025. 10. 1. 기준 수출 5.1% 증가"),
     "2026-11-01", KEPT),
    ("기사 문장에 그 연도가 있음 — 그대로",
     found("2015년 7월 1일 당시 환율은 1130원이었다.", "2015.07.01 1,130원"),
     "2026-11-01", KEPT),
]

CONDITION_CASES = [
    ("기준값으로만 맞춤(11/15 노인 빈곤)",
     found("노인 3명 중 1명 이상은 중위소득의 50%에도 못 미친다.", "중위소득 50% 미만", 기준값숫자="50%"), DROPPED),
    ("기준값이지만 다른 숫자로 확인함 — 그대로",
     found("노인 3명 중 1명 이상은 중위소득의 50%에도 못 미친다.", "중위소득 50% 미만 38.1%",
           기준값숫자="50%", 다른숫자로확인={"확인한숫자": "38.1%"}), KEPT),
]

METHOD_CASES = [
    ("한국갤럽이 전국 성인 1002명을 대상으로 조사했다.", True),          # 11/15 갤럽 표본 수
    ("조사 대상은 전국 초중고 교사 450명이다.", True),
    ("전국 50곳을 대상으로 실태를 점검했다.", True),
    ("10월 실업률은 3.2%로 전년보다 0.3%포인트 올랐다.", False),
    ("응답자의 43%가 그렇다고 답했다.", False),
]


def org_case(url: str, inst: str, site: str, sentence: str = "", extra: str = "") -> str:
    r = found(sentence or f"{inst}에 따르면 10%였다.", "인용", 상세페이지=url, PDF="")
    r["추출"].update(기관=inst, 기관누리집=site, 원어검색어=extra)
    return outcome(_guard_other_org(r, quiet=True))


OTHER_ORG_CASES = [
    ("등록청 자료를 인용한 연구단체 보고서(11/16 사전 점검 무작위 #13)",
     lambda: org_case("https://c4ads.org/wp-content/uploads/2026/09/x.pdf", "싱가포르 회계기업규제청", "acra.gov.sg"), DROPPED),
    ("기업 숫자를 실은 금융 정보 사이트(예전 헛답)",
     lambda: org_case("https://finance.yahoo.com/news/x", "엔비디아", "nvidia.com"), DROPPED),
    ("정책브리핑 사본 — 그대로", lambda: org_case("https://www.korea.kr/briefing/x", "관세청", "customs.go.kr"), KEPT),
    ("기관 자기 누리집(이름이 주소에 일부) — 그대로",
     lambda: org_case("https://www.nielsenkorea.co.kr/x", "닐슨코리아", "nielsen.com"), KEPT),
    ("영문 이름이 주소와 맞음 — 그대로",
     lambda: org_case("https://energyshift.institute/work/x", "에너지전환연구소", "esi.org", extra="Energy Shift Institute KEPCO"), KEPT),
    ("DART 공시 — 그대로", lambda: org_case("https://dart.fss.or.kr/dsaf001/main.do?rcpNo=1", "농심", "nongshim.com"), KEPT),
]


def news_case(url: str, sentence: str, page_text: str, institution_site: str = "") -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(page_text)
    r = found(sentence, "인용", 상세페이지=url, PDF=f.name)
    r["추출"]["기관누리집"] = institution_site
    out = outcome(_guard_news(r, quiet=True))
    Path(f.name).unlink()
    return out


NEWS_CASES = [
    ("같은 문장을 실은 기사(11/15 연합인포맥스)",
     lambda: news_case("https://news.einfomax.co.kr/news/articleView.html?idxno=1",
                       "달러-원 환율은 전일 대비 3.2원 오른 1,452.1원에 거래를 마쳤다.",
                       "홍길동 기자 = 달러-원 환율은 전일 대비 3.2원 오른 1,452.1원에 거래를 마쳤다. 무단전재 및 재배포 금지"),
     DROPPED),
    ("기사가 출처로 밝힌 외신의 기사 자체 — 그대로",
     lambda: news_case("https://www.theguardian.com/world/2026/nov/10/x",
                       "가디언에 따르면 영국 난민 신청은 10만8천건으로 늘었다.",
                       "Asylum applications rose to 108,000 in the year to September, the Home Office said."),
     KEPT),
    ("공공기관 보도자료 — 그대로",
     lambda: news_case("https://www.korea.kr/briefing/pressReleaseView.do?newsId=1",
                       "10월 고용률은 63.1%였다.", "10월 고용률은 63.1%로 전년 동월 대비 0.2%p 상승"),
     KEPT),
]


def check(name: str, got, want) -> str | None:
    return None if got == want else f"  {name}: 기대 {want}, 실제 {got}"


def main() -> int:
    bad = []
    bad += [check(n, outcome(_guard_year(r, d, quiet=True)), w) for n, r, d, w in YEAR_CASES]
    bad += [check(n, outcome(_guard_condition(r, quiet=True)), w) for n, r, w in CONDITION_CASES]
    bad += [check(s, only_method_numbers(s), w) for s, w in METHOD_CASES]
    bad += [check(n, f(), w) for n, f, w in NEWS_CASES]
    bad += [check(n, f(), w) for n, f, w in OTHER_ORG_CASES]
    bad += [check("공공기관 주소는 언론 아님", 일반경로.is_news_doc("https://gnews.gg.go.kr/briefing/a"), False)]
    bad = [b for b in bad if b]
    total = len(YEAR_CASES) + len(CONDITION_CASES) + len(METHOD_CASES) + len(NEWS_CASES) + len(OTHER_ORG_CASES) + 1
    print("\n".join(bad) if bad else "통과: 헛답 막기")
    print(f"\n사례 {total}개 — {'모두 통과' if not bad else f'{len(bad)}개 실패'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
