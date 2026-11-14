"""원문을 찾지 못했을 때 '왜 없는지, 어디서 확인하면 되는지'를 유형별로 알려 준다(A1·A2·A3, 11/14).

무작위 기사 60건에서 원문 쪽까지 못 간 이유는 대부분 '원문이 문서가 아님'이었다.
- 시세·조회 화면: 코스피·환율·금리·오피넷 유가·세이브로 순매수 → 그 날짜로 직접 조회할 화면 링크
- 기업 공시: 분기·사업보고서 → DART 회사별 공시 목록(라이너 site:dart.fss.or.kr 검색)
- 국가통계 원자료: 보도자료가 아니라 KOSIS 통계표에서 따로 뽑은 숫자 → 통계표 링크(라이너 site:kosis.kr 검색)
- 여론조사: 중앙선거여론조사심의위원회 등록 결과
- 업계·관계자: 공개 원문이 없을 가능성이 큼
attach(result) 는 '원출처 미확인' 결과에 result["안내"] = {유형, 설명, 링크} 를 붙인다.
to_raw_data(result) 는 '기사가 밝힌 자료에 숫자가 없음' + 국가통계면 '통계표·원자료 인용'으로 바꾼다.
"""
import re
import urllib.parse

from liner_api import web_search

MAX_LINKS = 4
KOSIS_SEARCH = "https://kosis.kr/search/search.do?query="

# (유형, 문장·기관에서 찾을 말, 설명, 고정 링크 [(제목, 주소, 이 말이 문장에 있을 때만)])
MARKET_LINKS = [
    ("한국거래소 정보데이터시스템(지수·시세·시가총액)", "https://data.krx.co.kr/", r"거래소|코스피|코스닥|KRX|시가총액|상장"),
    ("오피넷(주유소 판매가격)", "https://www.opinet.co.kr/", r"오피넷|주유소|휘발유|경유"),
    ("세이브로(해외주식 결제·보관 통계)", "https://seibro.or.kr/", r"세이브로|예탁결제원|순매수"),
    ("한국은행 경제통계시스템 ECOS(환율·금리)", "https://ecos.bok.or.kr/", r"환율|금리|국채|달러인덱스|원·달러|원/달러"),
    # 조회 화면에만 있는 통계(11/15 밤, 새 목표 ③ — 숫자는 못 보여 줘도 정확한 확인 화면을 알려 준다)
    ("한국부동산원 R-ONE 부동산통계(주간 아파트 동향 등)", "https://www.reb.or.kr/r-one/", r"부동산원|아파트값|아파트 가격|주간\s*동향|매매가격지수|전세가격"),
    ("국토교통부 실거래가 공개시스템", "https://rt.molit.go.kr/", r"실거래가|실거래|신고가|월세\s*\d|전세\s*거래"),
    ("서울 열린데이터광장(서울시 통계)", "https://data.seoul.go.kr/", r"서울시|서울특별시|자치구|구별"),
    ("건강보험심사평가원 보건의료빅데이터개방시스템(질병별 환자 수)", "https://opendata.hira.or.kr/", r"심평원|심사평가원|진료\s*환자|환자\s*수"),
    ("대학알리미(대학 공시)", "https://www.academyinfo.go.kr/", r"대학알리미|취업률|졸업자|전문대|대학\s*공시"),
]
MARKET = re.compile(r"화면번호|코스피|코스닥|시가총액|환율|국채금리|금리는|달러인덱스|오피넷|주유소|휘발유|"
                    r"공매도|거래대금|등락률|실거래가|실거래|아파트값|주간\s*동향|"
                    r"세이브로|순매수|마감했다|거래를 마감|장중|주가는?\s?[\d.]+%\s?(?:내렸|올랐|하락|상승|떨어|뛰었)")
# 연구자·보고서가 낸 분석이면 시세 화면이 아니다(11/14 중간 점검 #2: 연구위원이 말한 '주가 수익률 20~30%')
RESEARCH = re.compile(r"연구원|연구위원|연구진|교수|보고서|분석 결과|분석한 결과")
# '카드 매출'(카드사 집계)·'공시 위반'(거래소 발표)은 공시가 아니다 → 공시 문서 이름과 재무 항목만
DISCLOSURE = re.compile(r"분기보고서|반기보고서|사업보고서|감사보고서|공시했|공시됐|전자공시|영업이익|영업손실|순이익|매출액")
NOT_COMPANY = re.compile(r"거래소|공사|공단|협회|연구원|연구소|정부|위원회|[청부처]$")
POLL = re.compile(r"여론조사|갤럽|리얼미터|표본오차|신뢰수준|지지율")
INDUSTRY = re.compile(r"업계에 따르면|관계자에 따르면|업계는|업계 추산|소식통")
# 발언 인용(11/16 새 목표 ③): 따옴표 속 말 + 말했다·밝혔다 류, '시장은 "…"'
SPEECH = re.compile(r"[“\"][^”\"]{6,}[”\"]\s*(?:라고|이라고|며|고)?\s*(?:말했|밝혔|강조했|덧붙였|설명했|전했)|"
                    r"(?:시장|장관|대표|회장|의원|대통령|총리|위원장|사장|청장)(?:은|는|이|가)\s*[“\"]")
SOURCE_CUE = re.compile(r"따르면|에서|조사|통계|보고서|발표|집계|공시|밝혔|자료")
PUBLIC_DOMAIN = re.compile(r"\.go\.kr$|^kostat|^mods\.go|kosis")
STAT_WORDS = re.compile(r"통계|조사|동향|데이터처|통계청")


def _search_links(query: str, keep: str) -> list[dict]:
    """라이너 검색 결과 중 주소에 keep 이 든 것만."""
    try:
        rs = web_search(query)
    except RuntimeError as e:  # 안내 링크 검색 실패는 고정 링크만으로 계속
        print(f"    안내 링크 검색 실패 '{query}': {e}")
        return []
    out, seen = [], set()
    for r in rs:
        url = r.get("url", "")
        if keep in url and url not in seen and r.get("title") not in seen:   # 같은 제목(같은 회사 공시 목록) 한 번만
            seen.update({url, r.get("title")})
            out.append({"제목": r.get("title") or url, "주소": url})
    return out


def kosis_tables(info: dict) -> list[dict]:
    """KOSIS 통계표 화면(statHtml) 후보 + KOSIS 통합검색 주소."""
    words = " ".join(info.get("핵심어", [])[:3])
    q = " ".join(x for x in [info.get("보고서", ""), words, "통계표"] if x)
    tables = [x for x in _search_links(f"site:kosis.kr {q}", "kosis.kr") if "statHtml" in x["주소"]][:MAX_LINKS - 1]
    return tables + [{"제목": f"KOSIS에서 직접 찾기: {words}", "주소": KOSIS_SEARCH + urllib.parse.quote(words)}]


def is_public_stat(info: dict) -> bool:
    """국내 공공기관이 낸 통계인지(KOSIS 원자료가 있을 법한지)."""
    domain = (info.get("기관누리집") or "").lower()
    name = info.get("기관", "")
    return bool(PUBLIC_DOMAIN.search(domain) or re.search(r"데이터처|통계청", name)) and \
        bool(STAT_WORDS.search(info.get("보고서", "") + " ".join(info.get("핵심어", [])) + name))


def classify(sentence: str, info: dict) -> dict | None:
    """원문이 없을 때의 유형·설명·링크. 해당 없으면 None."""
    text = sentence + " " + info.get("기관", "")
    if MARKET.search(text) and not RESEARCH.search(sentence):
        links = [{"제목": t, "주소": u} for t, u, w in MARKET_LINKS if re.search(w, text)]
        if re.search(r"거래소|KRX|공매도|거래대금|등락률", text) and not any("krx" in x["주소"] for x in links):
            links.insert(0, {"제목": "한국거래소 정보데이터시스템(시세·공매도·거래대금 통계)", "주소": "https://data.krx.co.kr/"})
        return {"유형": "시세·조회 화면 자료",
                "설명": "그 시각의 시세·조회 화면에서 나온 숫자라 보고서 원문이 따로 없습니다. "
                        "기사 날짜로 아래 화면에서 직접 조회하면 확인할 수 있습니다.",
                "링크": links[:MAX_LINKS]}
    if POLL.search(text):
        return {"유형": "여론조사",
                "설명": "여론조사 결과는 중앙선거여론조사심의위원회에 등록된 결과표가 원문입니다.",
                "링크": [{"제목": "중앙선거여론조사심의위원회 여론조사결과 현황", "주소": "https://www.nesdc.go.kr/"}]}
    company = info.get("기관", "")
    domain = (info.get("기관누리집") or "").lower()
    if (company and DISCLOSURE.search(sentence) and not NOT_COMPANY.search(company)
            and not re.search(r"\.(go|re|or|ac)\.kr$|\.gov$", domain)):
        links = _search_links(f"site:dart.fss.or.kr {company} 분기보고서", "dart.fss.or.kr")
        links = [x for x in links if "searchNavi" in x["주소"]][:2] + [
            {"제목": "DART 공시통합검색", "주소": "https://dart.fss.or.kr/dsab007/main.do"}]
        return {"유형": "기업 공시",
                "설명": f"{company}의 실적·사업 숫자는 금융감독원 전자공시(DART)의 분기·사업보고서가 원문입니다. "
                        "아래 회사별 공시 목록에서 기사 시점의 보고서를 열어 확인할 수 있습니다.",
                "링크": links[:MAX_LINKS]}
    if is_public_stat(info):
        return {"유형": "국가통계 원자료",
                "설명": "보도자료·보고서에서 찾지 못했습니다. 기사가 국가통계포털(KOSIS) 통계표에서 직접 뽑은 숫자일 수 있습니다.",
                "링크": kosis_tables(info)}
    if SPEECH.search(sentence) and not re.search(r"보고서|조사|통계|공시|발표한\s*자료", sentence):
        return {"유형": "발언 인용",
                "설명": "인터뷰·발언·회견에서 말한 숫자로 보입니다. 공개 문서 원문이 따로 없을 수 있습니다. "
                        "발언한 기관의 보도자료나 회견 기록이 있으면 그곳이 원문입니다.",
                "링크": []}
    if not (info.get("기관") or "").strip() and not SOURCE_CUE.search(sentence):
        return {"유형": "출처를 밝히지 않은 숫자",
                "설명": "기사 문장에 숫자의 출처가 없습니다. 기사 앞뒤 문단에서 출처(기관·자료 이름)를 찾아 함께 넣으면 다시 찾아 볼 수 있습니다.",
                "링크": []}
    if INDUSTRY.search(sentence):
        return {"유형": "업계·관계자 자료",
                "설명": "업계·관계자가 기자에게 준 자료로 보입니다. 공개된 원문이 없을 가능성이 큽니다.",
                "링크": []}
    return None


def attach(result: dict, sentence: str) -> dict:
    """'원출처 미확인' 결과에 유형별 안내를 붙인다(결과 판정은 바꾸지 않음).
    원문 숫자는 확인 못 했지만 원문이 있을 곳(기관 누리집·공공기관·기사가 밝힌 외신의 그 페이지)을 찾았으면
    '확인할 원문 위치'로 맨 앞에 둔다(11/15 밤, 새 목표 ③)."""
    if result.get("판정") != "원출처 미확인" or result.get("안내"):
        return result
    spots = result.get("확인할위치") or []
    guide = classify(sentence, result.get("추출") or {})
    if spots:
        guide = guide or {"유형": "원문 위치", "설명": "원문이 있을 곳은 찾았지만 자동 접속이 막혔거나 숫자를 찾지 못했습니다. "
                                                  "아래 원문 위치에서 직접 확인해 주세요.", "링크": []}
        guide = {**guide, "원문위치": spots, "링크": spots + [x for x in guide["링크"] if x not in spots]}
    if guide:
        print(f"    안내: {guide['유형']} — 링크 {len(guide['링크'])}개")
        result["안내"] = guide
    return result


def to_raw_data(result: dict) -> dict:
    """기사가 밝힌 자료(확인된 문서)에 숫자가 없고 국가통계면: KOSIS 통계표가 있을 때 '통계표·원자료 인용'으로.
    (11/14 새 무작위 #25: 인구이동통계 보도자료에 없는 '1~9월 서울→경기 22만3416명' — KOSIS 시도별 이동자수 표)"""
    info = result.get("추출") or {}
    if result.get("판정") != "원문에 없음" or not is_public_stat(info):
        return result
    tables = kosis_tables(info)
    if not any("statHtml" in t["주소"] for t in tables):
        return result
    print("    확인한 보도자료에 숫자가 없고 국가통계 → '통계표·원자료 인용'(KOSIS 통계표 후보)")
    result.update(판정="통계표·원자료 인용", 통계표후보=tables,
                  이유=f"기사가 밝힌 자료({result.get('보고서', '')})에는 이 숫자가 없습니다. "
                       "같은 통계의 KOSIS 통계표에서 따로 뽑은 숫자로 보입니다.")
    return result
