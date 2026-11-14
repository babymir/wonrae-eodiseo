"""대한민국 정책브리핑(korea.kr) 보도자료 검색 — 정부 부처·청·처 보도자료 원문(본문 + 첨부 HWP·PDF)을 찾는다(11/14).

라이너 웹 검색은 정부 게시판 보도자료를 거의 색인하지 못한다(원문식 숫자 '154,987'로 찾아도 기사만 나옴).
정책브리핑은 모든 부처·청 보도자료를 한곳에 모으고 자체 검색이 된다.
- 목록: /briefing/pressReleaseList.do?srchWord=검색어&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD
  ('검색결과 총 0 건'이면 검색어와 상관없는 최신 목록이 나오므로 버린다)
- 게시물: /briefing/pressReleaseView.do?newsId=… (본문 + 첨부 내려받기 → 일반경로.fetch_doc 이 같이 읽음)
"""
import hashlib
import html as html_lib
import re
import urllib.parse
from datetime import date, timedelta

from 기관별 import Candidate, fetch_bytes
from 후보문서 import article_day

BASE = "https://www.korea.kr"
LIST = BASE + "/briefing/pressReleaseList.do?srchWord={q}&startDate={start}&endDate={end}"
VIEW = BASE + "/briefing/pressReleaseView.do?newsId={id}"
DAYS_BEFORE = 60          # 기사 날짜 이전 몇 일까지 찾을지(핵심어 검색)
REPORT_DAYS_BEFORE = 365  # 기사가 밝힌 보고서·조사 이름으로 찾을 때(11/14 무작위3: 3월 보도자료를 11월 기사가 인용)
MAX_RESULTS = 4
ITEM = re.compile(r"pressReleaseView\.do\?newsId=(\d+)[^>]*>\s*(?:<[^>]+>\s*)*([^<]{4,120})")
GOV_NAME = re.compile(r"부$|처$|청$|위원회$|본부$|실$|^국무|^대통령")


def is_government(info: dict) -> bool:
    """정부 부처·청·처(정책브리핑에 보도자료가 올라오는 곳)인지: 이름 끝말 또는 누리집 .go.kr."""
    name = re.sub(r"\(.*?\)|\s", "", info.get("기관", ""))
    domain = (info.get("기관누리집") or "").lower()
    return bool(GOV_NAME.search(name) or domain.endswith(".go.kr"))


def _window(info: dict, article_date: str, days: int = DAYS_BEFORE) -> tuple[str, str]:
    end = article_day(article_date) or date.today()
    start = end - timedelta(days=days)
    return start.isoformat(), (end + timedelta(days=1)).isoformat()


def _queries(info: dict) -> list[str]:
    out = []
    if info.get("보고서"):
        out.append(re.sub(r"[「」『』‘’'\"]", "", info["보고서"]))
    words = [w for w in info.get("핵심어", []) if re.search(r"[가-힣]", w)][:3]
    if words[:2]:
        out.append(" ".join(words[:2]))
    out += words   # 핵심어 하나씩도(11/14 무작위3: '정유사 공급가격 상한'은 0건, '석유 최고가격'이 보도자료 제목)
    return list(dict.fromkeys(q for q in out if q))


def search(info: dict, article_date: str) -> list[Candidate]:
    """보도자료 게시물 후보(기사 날짜 이전 DAYS_BEFORE 일). 검색어를 좁은 것부터 넓은 것까지 차례로."""
    found: dict[str, str] = {}
    report_q = re.sub(r"[「」『』‘’'\"]", "", info.get("보고서") or "")
    for q in _queries(info):
        start, end = _window(info, article_date, REPORT_DAYS_BEFORE if q == report_q else DAYS_BEFORE)
        try:
            page = fetch_bytes(LIST.format(q=urllib.parse.quote(q), start=start, end=end)).decode("utf-8", "replace")
        except RuntimeError as e:  # 정책브리핑 실패는 다른 경로로 계속(이유 출력)
            print(f"    정책브리핑 검색 실패 '{q}': {e}")
            continue
        total = re.search(r"검색결과\s*총\s*(?:<[^>]+>\s*)*([\d,]+)", page)
        n = int(total.group(1).replace(",", "")) if total else 0
        print(f"    정책브리핑 보도자료 검색 '{q}' ({start}~{end}) → {n}건")
        if n == 0:
            continue   # 결과가 없으면 최신 목록이 대신 나온다 → 쓰지 않음
        for nid, title in ITEM.findall(page):
            found.setdefault(nid, html_lib.unescape(title).strip())
        if len(found) >= MAX_RESULTS:
            break
    out = []
    for nid, title in list(found.items())[:MAX_RESULTS]:
        url = VIEW.format(id=nid)
        out.append(Candidate(title, url, url, "gen_" + hashlib.sha1(url.encode()).hexdigest()[:12]))
    return out
