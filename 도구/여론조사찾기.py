"""여론조사 숫자 → 중앙선거여론조사심의위원회(nesdc.go.kr) 등록 결과분석 PDF(11/14).

선거·정당 여론조사는 공표 전에 심의위에 등록하고, 등록 게시물에 '결과분석' PDF(질문별 결과표)가 붙는다.
라이너 웹 검색은 이 게시판을 거의 색인하지 못한다(11/14 무작위3: 한길리서치 6.6%·52.7% 둘 다 놓침).
- 조사기관(한길리서치·한국갤럽 …)이나 의뢰자(쿠키뉴스 …) 이름으로 게시판 검색, 기사 날짜 앞 SEARCH_DAYS 일 등록분
- 게시물마다 첨부 PDF(결과분석 우선) → 후보(문서 주소 = 첨부 내려받기, 사람에게 보여 줄 주소 = 등록 게시물)
"""
import hashlib
import re
import urllib.parse
from datetime import date, timedelta

from 기관별 import Candidate, fetch_bytes
from 후보문서 import article_day

BASE = "https://www.nesdc.go.kr"
LIST = (BASE + "/portal/bbs/B0000005/list.do?menuNo=200467&searchCnd={cnd}&searchWrd={q}"
        "&searchTime=1&sdate={start}&edate={end}")
VIEW = BASE + "/portal/bbs/B0000005/view.do?nttId={id}&menuNo=200467"
DOWN = BASE + "/portal/cmm/fms/FileDown.do?atchFileId={a}&fileSn={b}&bbsId={c}&bbsKey={d}"
SEARCH_DAYS = 21
MAX_POSTS = 3
POLL_WORDS = re.compile(r"여론조사|지지율|지지도|표본오차|신뢰수준|응답률|국정\s*수행|정당\s*지지")
# 등록 게시판 '조사기관명'으로 찾을 이름(기사 표기 → 검색어). 앞의 것이 우선
POLLSTERS = [("한국갤럽", "갤럽"), ("갤럽", "갤럽"), ("리얼미터", "리얼미터"), ("한길리서치", "한길리서치"),
             ("엠브레인", "엠브레인"), ("케이스탯", "케이스탯"), ("코리아리서치", "코리아리서치"),
             ("한국리서치", "한국리서치"), ("NBS", "엠브레인"), ("전국지표조사", "엠브레인"),
             ("넥스트리서치", "넥스트리서치"), ("에이스리서치", "에이스리서치"), ("조원씨앤아이", "조원씨앤아이"),
             ("여론조사꽃", "여론조사꽃"), ("미디어토마토", "미디어토마토"), ("알앤써치", "알앤써치"),
             ("리서치뷰", "리서치뷰"), ("윈지코리아", "윈지코리아"), ("입소스", "입소스"),
             ("리서치앤리서치", "리서치앤리서치"), ("메타보이스", "메타보이스"), ("데일리리서치", "데일리리서치"),
             ("시그널앤펄스", "시그널앤펄스"), ("피앤알", "피앤알"), ("PNR", "피앤알"), ("리서치웰", "리서치웰"),
             ("한국사회여론연구소", "한국사회여론연구소"), ("KSOI", "한국사회여론연구소"), ("코리아정보리서치", "코리아정보리서치")]
ITEM = re.compile(r"view\.do\?nttId=(\d+)")
ATTACH = re.compile(r"view\('([^']+)',\s*'([^']+)',\s*'([^']+)',\s*'([^']+)'\);\"\s*>\s*([^<]+?)\s*</a>")


def is_poll(sentence: str) -> bool:
    return bool(POLL_WORDS.search(sentence)) or any(name in sentence for name, _ in POLLSTERS)


def _pollster(text: str) -> str:
    for name, query in POLLSTERS:
        if name in text:
            return query
    return ""


def _client(text: str) -> str:
    """의뢰 언론사: '쿠키뉴스가 한길리서치에 의뢰해' → 쿠키뉴스."""
    m = re.search(r"([가-힣A-Za-z0-9]{2,12}?)(?:이|가|는|와|과)?\s+[가-힣A-Za-z]+(?:에|에게)\s*의뢰", text)
    return m.group(1) if m else ""


def _posts(cnd: str, q: str, start: str, end: str) -> list[str]:
    page = fetch_bytes(LIST.format(cnd=cnd, q=urllib.parse.quote(q), start=start, end=end)).decode("utf-8", "replace")
    return list(dict.fromkeys(ITEM.findall(page)))


def search(info: dict, sentence: str, context: str, article_date: str) -> list[Candidate]:
    """여론조사가 아니거나 조사기관을 모르면 빈 목록. 조사기관·의뢰자는 문장 → 분해한 기관 → 문맥 순으로 찾는다
    (11/14: 문맥의 다른 조사기관 '리얼미터'를 잡아 한길리서치 조사를 놓침)."""
    if not is_poll(sentence + " " + context):
        return []
    who = _pollster(sentence) or _pollster(info.get("기관", "")) or _pollster(context)
    client = _client(sentence) or (_client(context) if not _pollster(sentence) else "")
    if not who and not client:
        return []
    end = article_day(article_date) or date.today()
    start = (end - timedelta(days=SEARCH_DAYS)).isoformat()
    ids: list[str] = []
    try:
        if who and client:   # 둘 다 알면 의뢰자로 먼저(같은 조사기관의 다른 의뢰 조사를 피함)
            ids = _posts("2", client, start, end.isoformat())
        if not ids and who:
            ids = _posts("1", who, start, end.isoformat())
        if not ids and client:
            ids = _posts("2", client, start, end.isoformat())
    except RuntimeError as e:  # 심의위 실패는 다른 경로로 계속(이유 출력)
        print(f"    여론조사심의위 검색 실패: {e}")
        return []
    print(f"    여론조사심의위 등록 검색(조사기관 '{who}', 의뢰자 '{client}', {start}~{end}) → {len(ids)}건")
    out = []
    for nid in ids[:MAX_POSTS]:
        view = VIEW.format(id=nid)
        try:
            page = fetch_bytes(view).decode("utf-8", "replace")
        except RuntimeError as e:
            print(f"    여론조사 등록 게시물 받기 실패({nid}): {e}")
            continue
        files = [(DOWN.format(a=a, b=b, c=c, d=d), name) for a, b, c, d, name in ATTACH.findall(page)
                 if name.lower().endswith(".pdf")]
        files.sort(key=lambda f: "결과" not in f[1] and "분석" not in f[1])   # 결과분석 PDF 먼저
        for url, name in files[:1]:
            out.append(Candidate(f"여론조사 등록 {nid} — {name}", view, url,
                                 "gen_" + hashlib.sha1(url.encode()).hexdigest()[:12]))
    return out
