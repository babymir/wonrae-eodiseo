"""닐슨코리아 시청률 숫자 → 그날의 '일일 시청률 TOP 20' 공개 화면(11/15).

닐슨코리아 누리집의 일일 순위 화면은 날짜를 주소로 고를 수 있다(begin_date=YYYYMMDD).
기본 화면은 '어제' 순위라, 날짜를 지정하지 않으면 며칠 지난 기사 숫자는 사라진다(무작위3 #16 닥터X 14.5%).
- 날짜: 문장의 'M월 D일 방송' → 그날, '전날 방송' → 기사 날짜 하루 전, 없으면 기사 날짜 하루 전
- 화면: 지상파 일일 순위(전국 area=00, 수도권 area=01 — 누리집 탭으로 확인)
"""
import hashlib
import re
from datetime import timedelta

from 기관별 import Candidate
from 후보문서 import article_day

PAGE = "https://www.nielsenkorea.co.kr/tv_terrestrial_day.asp?menu=Tit_1&sub_menu=1_1&area={area}&begin_date={day}"
CUE = re.compile(r"닐슨\s*코리아|닐슨코리아")


def search(sentence: str, article_date: str) -> list[Candidate]:
    if not CUE.search(sentence) or "시청률" not in sentence:
        return []
    base = article_day(article_date)
    if not base:
        return []
    m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", sentence)
    day = base.replace(month=int(m.group(1)), day=int(m.group(2))) if m else base - timedelta(days=1)
    areas = ["01", "00"] if "수도권" in sentence else ["00", "01"]
    out = []
    for area in areas:
        url = PAGE.format(area=area, day=day.strftime("%Y%m%d"))
        out.append(Candidate(f"닐슨코리아 지상파 일일 시청률 TOP 20 ({day.isoformat()}, {'전국' if area == '00' else '수도권'})",
                             url, url, "gen_" + hashlib.sha1(url.encode()).hexdigest()[:12]))
    print(f"    닐슨코리아 일일 시청률 화면({day.isoformat()}) 후보 {len(out)}건")
    return out
