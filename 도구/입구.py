"""입구 필터: 확인할 문장이 어떤 종류인지 먼저 가린다(6경로).

통계 사실 문장만 원문 대조로 보내고, 나머지는 '확인 대상 아님(사유)'으로 답한다.
11/13 평가에서 정책 제안·행정 소식·조사 실시 안내에도 '원문에 없음'으로 답해 기사가 틀린 것처럼 보였다.
근거: Full Fact 문장 7분류(수량 문장 F1 0.79, 전망 0.37) — 숫자만 보고 통계로 넘기면 안 된다.
  reports/원래 어디서 약점 연구 사례.md

경로: 통계 사실 / 전망·목표 / 정책 제안 / 행정·행사 소식 / 인용·발언(의견) / 개인 사례
1) 학술지·논문 인용 → 지원 범위 밖(KCI 검색 링크)
2) 통계 단위가 붙은 숫자가 없으면 → 통계 문장 아님
3) 규칙: 제안·행정·전망 표지어(따옴표 속 보고서 제목은 빼고 봄). 단 '에 따르면·나타났다' 같은
   통계 결과 표현이 있으면 통계 사실로 본다.
4) 통계 표현이 있으면 → 통계 사실
5) 그래도 애매하면 → 라이너 Decisions 로 6경로 중 하나
"""
import re
import urllib.parse

from liner_api import decide

STAT_NUMBER = re.compile(
    r"\d[\d,.]*\s*(?:%p|%포인트|%|퍼센트|배|명|만\s*명|천\s*명|원|만\s*원|억|조|달러|건|가구|곳|개사|개소|"
    r"세|시간|일|년|위|점|톤|㎡|kg|가지|회)")
STAT_WORDS = re.compile(
    r"조사|나타났|집계|기록|증가|감소|늘었|늘어|늘며|줄었|줄어|상승|하락|올랐|내렸|비율|비중|응답|차지|"
    r"에\s*따르면|분석|추정|추산|평균|중위|지수|률|율|규모|수준|격차|전년|대비|이상|미만|가량|불과|그쳤|달했")
# 통계 '결과'를 전한다는 강한 표현: 이것이 있으면 제안·행정 표지어가 있어도 통계 사실로 본다
STAT_STRONG = re.compile(r"에\s*따르면|에\s*의하면|보고서를\s*보면|나타났|조사\s*결과|분석\s*결과|분석한\s*결과|집계됐|집계되었|추산했|추정됐|기록했")
PROPOSAL = re.compile(
    r"하자고|하자는|해야\s*한다|해야\s*합니다|제안했|제안한|제안하|촉구|건의|대안을\s*제시|방안을\s*제시|방안이\s*제시|"
    r"올리고|낮추고|완화하고|높이는\s*대안|상향하는|규정합니다|규정한다|도입하자|확대하자")
ADMIN = re.compile(
    r"표창|포상|수상했|시상|선정됐|선정되었|선정해|위촉|임명|개최|실시한다|실시할|실시했다고|실시하기로|"
    r"모집한다|접수한다|대상으로\s*진행")
FORECAST = re.compile(r"전망이다|전망된다|전망했|예상된다|예상했|예측했|할\s*것으로\s*보인다|목표로|목표치|계획이다|하겠다|방침이다")
TITLE_QUOTES = re.compile(r"[‘'「『《〈][^’'」』》〉]{2,80}[’'」』》〉]")
JOURNAL = re.compile(r"학술지|논문|게재|저널|학회지|『보건사회연구』|‘보건사회연구’|'보건사회연구'")
KCI_SEARCH = "https://www.kci.go.kr/kciportal/po/search/poTotalSearList.kci?sereArticleSearchBean.keyword="

ROUTES = {
    "통계 사실": "조사·통계·연구 결과로 나온 숫자(비율·인원·금액·증감 등)를 사실로 전한다",
    "전망·목표": "앞으로의 예측·전망·목표치·계획을 말한다",
    "정책 제안": "제도를 바꾸자는 제안·대안·요구를 말한다(숫자는 제안 내용의 일부)",
    "행정·행사 소식": "포상·선정·행사·조사 실시 안내·인사처럼 기관 소식을 전한다",
    "인용·발언": "사람의 의견·주장을 전하며 숫자가 조사 결과로 제시되지 않았다",
    "개인 사례": "한 사람·한 가구의 개인 경험을 전한다",
}
ROUTE_REASON = {
    "전망·목표": "앞으로의 전망·목표를 말하는 문장이라 이미 나온 통계 원문과 대조할 대상이 아닙니다.",
    "정책 제안": "제도를 바꾸자는 제안을 전하는 문장입니다. 숫자는 제안 내용이라 통계 원문과 대조할 대상이 아닙니다.",
    "행정·행사 소식": "기관 소식(포상·조사 실시 안내 등)을 전하는 문장이라 통계 원문과 대조할 대상이 아닙니다.",
    "인용·발언": "사람의 의견을 전하는 문장이라 통계 원문과 대조할 대상이 아닙니다.",
    "개인 사례": "한 사람의 사례를 전하는 문장이라 통계 원문과 대조할 대상이 아닙니다.",
    "통계 문장 아님": "통계 단위(%, 명, 원, 배 등)가 붙은 숫자가 없습니다.",
    "조사 방법": "숫자가 모두 조사 방법(표본 수·신뢰수준·표본오차·조사 기간)이라 대조할 결과 숫자가 없습니다. 결과는 앞뒤 문장에 있습니다.",
}
DECISION_MIN = 0.5
# 기관·회사가 '밝혔다·발표·공시'한 숫자는 제안·행정 소식·발언이라도 원 보도자료·공시가 있다(11/14 무작위3:
# 산업부 석유 최고가격 1784원, 넷리스트 특허료 4490만 달러). 이런 문장은 막지 않고 원문을 찾는다.
ANNOUNCE = re.compile(r"밝혔|발표|공시|공개했|공개한|브리핑|보도자료|고시했|확정했|집계했")
SOFT_ROUTES = {"정책 제안", "행정·행사 소식", "인용·발언"}


def _announced(sentence: str) -> dict:
    return {"통과": True, "경로": "공식 발표", "방법": "규칙",
            "이유": "기관·회사가 발표한 숫자라 원 보도자료·공시에서 찾음"}


METHOD_NUMBER = re.compile(r"[\d,.]+\s*(?:명|가구|곳|개사)\s*(?:을|를)?\s*대상|신뢰\s*수준|표본\s*오차|응답률|"
                           r"[±+-]\s*[\d.]+\s*%\s*(?:포인트|p)|조사\s*기간|\d+\s*[~∼-]\s*\d+\s*일|"
                           # 연구 설계 숫자(11/15 밤 신규 세트): '조사 대상은 … 450명', '시험 24편에 참여한 환자 1,815명',
                           # '약 4만 7000명을 추적한 결과' — 결과가 아니라 대상·규모
                           r"(?:조사|연구|분석)\s*대상은|[\d,.]+\s*편(?:에|의|을)|참여한\s*(?:환자|사람|대상자|참가자)|"
                           r"[\d,.만천]+\s*명(?:을|를)\s*(?:추적|조사|분석|모집)")


DESIGN_CUE = re.compile(r"(?:조사|연구|분석|실험)\s*대상은|참여한\s*(?:환자|사람|대상자|참가자)|명(?:을|를)\s*(?:추적|모집)|"
                        r"편(?:에|의)\s*참여|모아\s*분석|대상으로\s*(?:실시|진행|조사)")
COUNT_UNIT = re.compile(r"(?:명|가구|곳|개사|편|건|개국)$")


def only_method_numbers(sentence: str) -> bool:
    """문장의 단위 붙은 숫자가 모두 조사 방법(…명을 대상으로, 95% 신뢰수준, 표본오차 ±3.1%p)인지
    (11/15: 갤럽 조사 개요 문장에서 표본 수 1,002명을 '일치'로 냄 — 결과 숫자는 앞뒤 문장에 있음)."""
    from 기사정리 import all_numbers   # 입구는 가볍게 불러오려고 여기서
    nums = all_numbers(sentence)
    if not nums:
        return False
    if DESIGN_CUE.search(sentence) and all(COUNT_UNIT.search(n) for n in nums):
        return True   # 연구 설계 표현이 있고 숫자가 모두 사람·곳·편 수(결과 비율·금액이 없음)
    method_spans = [m.span() for m in METHOD_NUMBER.finditer(sentence)]
    # 숫자가 조사 방법 표현 안에 있거나 바로 앞(공백·'에' 정도만 사이)에 붙어 있으면 방법 숫자
    attached = lambda st, en: any(a <= st <= b or 0 <= a - en <= 3 for a, b in method_spans)   # noqa: E731
    claims = [n for n in nums if not any(attached(m.start(), m.end()) for m in re.finditer(re.escape(n), sentence))]
    return bool(method_spans) and not claims


def _not_target(route: str, how: str, extra: str = "") -> dict:
    return {"통과": False, "종류": "확인 대상 아님", "경로": route, "방법": how,
            "이유": ROUTE_REASON[route] + (f" ({extra})" if extra else "")}


def route_by_rule(sentence: str) -> str | None:
    """규칙으로 정해지는 경로. 못 정하면 None. 보고서 제목(따옴표 속)은 빼고 본다."""
    body = TITLE_QUOTES.sub(" ", sentence)
    if STAT_STRONG.search(body):
        return "통계 사실"
    for route, pat in (("정책 제안", PROPOSAL), ("행정·행사 소식", ADMIN), ("전망·목표", FORECAST)):
        if pat.search(body):
            return route
    if STAT_WORDS.search(body):
        return "통계 사실"
    return None


def check(sentence: str) -> dict:
    """{"통과": bool, "종류", "경로", "이유", "방법": "규칙"|"라이너 Decisions", "링크": [...]}"""
    if JOURNAL.search(sentence):
        # 11/14: 논문도 원문(초록)에서 찾는다(논문찾기.py, PubMed). 못 찾을 때를 위해 KCI 링크를 같이 둔다
        words = re.sub(r"[^가-힣A-Za-z0-9 ]", " ", sentence)[:60]
        return {"통과": True, "경로": "학술지", "방법": "규칙", "이유": "학술지 논문을 인용한 문장",
                "링크": [{"제목": "KCI(한국학술지인용색인)에서 논문 찾기",
                          "주소": KCI_SEARCH + urllib.parse.quote(words.strip())}]}
    if not STAT_NUMBER.search(sentence):
        return _not_target("통계 문장 아님", "규칙")
    if only_method_numbers(sentence):
        return _not_target("조사 방법", "규칙")
    route = route_by_rule(sentence)
    if route == "통계 사실":
        return {"통과": True, "경로": route, "방법": "규칙", "이유": "통계 숫자와 통계 결과 표현이 있음"}
    if route in SOFT_ROUTES and ANNOUNCE.search(sentence):
        return _announced(sentence)
    if route:
        return _not_target(route, "규칙")
    try:
        ans = decide({"문장": sentence}, "문장종류",
                     "이 기사 문장이 어떤 종류인지 고르라. 숫자가 있어도 조사·통계 결과가 아니면 통계 사실이 아니다.",
                     ROUTES)
    except RuntimeError as e:  # 판단 못 하면 막지 않고 통과(이유 출력)
        print(f"    입구 필터 Decisions 실패, 통과시킴: {e}")
        return {"통과": True, "경로": "통계 사실", "방법": "규칙", "이유": "애매하지만 판단 실패로 통과"}
    probs = {k: float(v) for k, v in (ans.get("probabilities") or {}).items()}
    stat = probs.get("통계 사실", 0.0)
    if stat >= DECISION_MIN or not probs:
        return {"통과": True, "경로": "통계 사실", "방법": "라이너 Decisions", "이유": f"통계 사실일 확률 {stat:.2f}"}
    best = max(probs, key=probs.get)
    if best in SOFT_ROUTES and ANNOUNCE.search(sentence):
        return _announced(sentence)
    return _not_target(best if best in ROUTE_REASON else "통계 문장 아님", "라이너 Decisions",
                       f"통계 사실일 확률 {stat:.2f}, '{best}' {probs[best]:.2f}")


if __name__ == "__main__":  # 규칙만 확인(Decisions 호출 없이)
    for s in ["이에 최소 할증률을 30%에서 40%로 올리고 법률로 규정합니다.",
              "국가데이터처는 오는 20일부터 전국 약 23만 2000가구를 대상으로 '지역별고용조사'를 실시한다고 16일 밝혔다.",
              "소속 공무원과 조사요원 13명도 개인표창을 받았다.",
              "한국보건사회연구원이 9일 발간한 ‘쉬었음 미혼 청년의 미래 전망과 가족 형성’ 보고서에 따르면 5.5%가 쉬었음 상태였다.",
              "국가데이터처에 따르면 지난 10월 국내 소비자물가지수는 전년 동월 대비 2.8% 상승했다.",
              "생계급여는 현행 기준 중위소득 32%에서 35%로 단계적으로 올리고, 주거급여는 50%로 상향하는 방안이 제시됐다.",
              "올해 평가는 214개 국가통계 작성기관을 대상으로 진행됐으며 심사를 거쳐 5건이 선정됐다.",
              "정부 연구용역을 수행한 자본시장연구원은 PBR 하위 25%로 완화하고, 최소 할증률은 40%로 높이는 대안을 제시했습니다."]:
        print(f"{route_by_rule(s)!s:10} ← {s[:60]}")
