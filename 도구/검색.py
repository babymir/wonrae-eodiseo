"""라이너 검색: 기사에서 뽑은 정보로 검색어를 여러 개 만들어 동시에 보낸다.

- 기관 도메인(site:·filetype:pdf), 기사가 밝힌 제목 그대로, 일반·숫자형 검색, 막 나온 자료용 '최근 한 달'
- 지원 밖 기관(기본 길)은 일반경로.py 가 찾는다
"""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from liner_api import web_search
from 기관별 import DOMAINS
from 후보문서 import WORKERS, article_day

RECENT_DAYS = 40          # 기사가 이 기간 안이면 '최근 한 달' 조건 검색을 더 한다(막 나온 자료)


SKIP_LEAD = re.compile(r"instagram|facebook|youtube|x\.com|twitter|blog|tistory|threads")


def is_recent(article_date: str) -> bool:
    d = article_day(article_date)
    return d is not None and 0 <= (date.today() - d).days <= RECENT_DAYS


def search_all(info: dict, inst: str | None, recent: bool) -> tuple[list[dict], list[dict]]:
    """(기관 도메인 검색 결과, 일반 검색 결과). 검색어 여러 개를 동시에 보낸다.
    - 기관 PDF(site:+filetype:pdf), 기관 페이지(site:), 일반(기관·보고서·핵심어·숫자)
    - 숫자형: 같은 숫자를 쓴 다른 기사·자료(보고서 이름이 없는 기사 대비)
    - 기사가 최근이면: 기관 site: 검색을 '최근 한 달' 조건으로 한 번 더(막 나온 보도자료)"""
    terms = info.get("보고서") or " ".join(info["핵심어"][:2])
    period = info.get("기준시점", "")
    general_q = " ".join(x for x in [info["기관"], info.get("보고서", ""), *info["핵심어"], info["숫자"]] if x)
    number_q = " ".join(x for x in [info["숫자"], *info["핵심어"][:3], info["기관"]] if x)
    site_qs: list[tuple[str, str | None]] = []
    if inst in DOMAINS:
        d = DOMAINS[inst]
        if info.get("보고서"):  # 기사가 제목을 밝혔으면 제목 그대로 먼저(정답 보고서가 후보에 들어와야 제목 우선 규칙이 작동)
            site_qs.append((f'site:{d} "{info["보고서"]}"', None))
        site_qs += [(f"site:{d} filetype:pdf {terms} {period}".strip(), None),
                    (f"site:{d} {terms} {period} {' '.join(info['핵심어'][:3])}".strip(), None)]
        if recent:
            site_qs.append((f"site:{d} {terms} {' '.join(info['핵심어'][:2])}".strip(), "past_month"))
    general_qs = [(general_q, None), (number_q, None)]

    def one(q: tuple[str, str | None]) -> list[dict]:
        try:
            rs = web_search(q[0], date_range=q[1])
        except RuntimeError as e:   # 검색 하나 실패는 나머지로 계속(이유 출력)
            print(f"    라이너 검색 실패 '{q[0]}': {e}")
            return []
        print(f"    라이너 검색 '{q[0]}'{' (최근 한 달)' if q[1] else ''} → {len(rs)}건")
        return rs

    with ThreadPoolExecutor(WORKERS) as ex:
        answers = list(ex.map(one, site_qs + general_qs))
    site_results, general, seen = [], [], set()
    for i, rs in enumerate(answers):
        for r in rs:
            if r.get("url") in seen:
                continue
            seen.add(r.get("url"))
            (site_results if i < len(site_qs) else general).append(r)
    return site_results, general
