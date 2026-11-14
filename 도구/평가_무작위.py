"""무작위 평가 세트 만들기 — 기관을 가리지 않고 '숫자 + 출처 표현'이 든 기사 문장을 뽑는다.

목적: 지금까지 세트는 세 기관(자본연·데이터처·보사연) 인용 기사만 골랐다.
      이 세트는 '아무 기사나 넣었을 때' 성적과 출처 기관 분포를 잰다.
- 기사 찾기: 다음(Daum) 뉴스 검색 최신순, 기관 이름이 아닌 출처 표현 검색어(SOURCE_QUERIES)
- 문장 고르기: 규칙만 사용(라이너 모델 아님). 기사마다 '숫자+단위'와 출처 표현이 같이 든 첫 문장 1개
- 후보를 넉넉히 모은 뒤 고정 시드로 무작위 N건 → 누가 다시 돌려도 같은 세트
- 이미 만든 평가 세트(작업/평가*/평가대상.json)와 같은 기사 제목·문장은 뺀다

사용: python3 도구/평가_무작위.py [결과 폴더] [--방식=B]   (기본 작업/평가_무작위, 방식 A)
      방식 A: 최신순 + 출처 표현 검색어(첫·둘째 세트)
      방식 B: 정확도순(여러 날짜) + 다른 검색어 + 다른 시드(셋째 세트, 11/22 동결 뒤 한 번만 잴 시험 세트)
      방식 C: 최신순 + 또 다른 검색어 + 다른 시드, 40건(넷째 세트, 개발용 — 11/14 밤 85% 목표 점검)
      방식 D: 최신순 + 또 다른 검색어 + 다른 시드, 30건(검증 세트 — 11/15. 보면서 고치지 않고 일반 규칙의 효과만 잰다)
      방식 E: 최신순 + 또 다른 검색어 + 다른 시드, 40건(신규 세트 — 11/15 밤. 결과를 보고 고치지 않는다. 정직한 실력 측정)
      방식 F: 최신순 + 또 다른 검색어 + 다른 시드, 40건(신규 세트2 — 새 목표 세 지표 측정용, 보고 고치지 않음)
      방식 G: 최신순 + 공공·연구기관·보고서 출처 검색어, 30건(인용 장면 세트 — 11/15. 보고서에 인용하려는 숫자처럼
              공공·연구기관·국제기구·보고서·실태조사를 출처로 밝힌 문장만. 라이너 Deep Research 와 비교용, 보고 고치지 않음)
결과: <결과 폴더>/평가대상.json → python3 -u 도구/평가_실행.py <결과 폴더>
중간 저장: 받은 기사는 <결과 폴더>/기사캐시/ 에 저장해 다시 받지 않는다.
"""
import json
import random
import re
import sys
import urllib.parse
from pathlib import Path

# 평가_새기사.py 의 기사 받기·정리 함수를 그대로 쓴다(그 파일은 sys.argv[1] 을 결과 폴더로 읽음)
_FLAGS = [a for a in sys.argv[1:] if a.startswith("--")]
_FOLDER = next((a for a in sys.argv[1:] if not a.startswith("--")), "작업/평가_무작위")
sys.argv = [sys.argv[0], _FOLDER]
METHOD = next((a.split("=", 1)[1] for a in _FLAGS if a.startswith("--방식=")), "A")
from 평가_새기사 import DAUM_SEARCH, MAX_SENT, MIN_SENT, NUMBER, OUT_DIR, SKIP_WORDS, fetch_html, parse_article # noqa: E402 (sys.argv 를 먼저 정리해야 함)

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_SIZE = {"C": 40, "E": 40, "F": 40, "G": 30}.get(METHOD, 30)   # 뽑을 문장 수
POOL_TARGET = 160         # 무작위로 뽑기 전에 모을 후보 수
PAGES = 8                 # 검색어마다 볼 검색 결과 쪽 수
SEED = {"A": 20261114, "B": 20261122, "C": 20261115, "D": 20261116, "E": 20261117, "F": 20261118, "G": 20261119, "H": 20261120, "I": 20261121}[METHOD]   # 고정 시드(재현용)
SORT = "accuracy" if METHOD == "B" else "recency"
MAX_PER_QUERY = 30        # 한 검색어가 후보를 독차지하지 않게
SOURCE_QUERIES = {"A": ["따르면", "조사 결과", "집계", "보고서", "통계", "분석 결과", "발표"],
                  "B": ["자료를 보면", "조사됐다", "전년 대비", "집계됐다", "나타났다", "추산", "통계에 따르면"],
                  "C": ["밝혔다 비율", "응답자", "증가했다", "감소했다", "역대 최대", "연구 결과", "보도자료",
                        "실태조사", "전망"],
                  "D": ["발표에 따르면", "통계청", "보고서에 따르면", "조사에서", "전년 동기", "분석에 따르면",
                        "연구원은", "집계 결과", "이상으로 나타났다"],
                  "E": ["자료에 따르면", "설문 결과", "늘어난 것으로", "줄어든 것으로", "비중은", "만에 최대",
                        "공개한 자료", "통계를 보면", "조사해 발표"],
                  "F": ["발표했다", "기록했다", "집계했다", "조사를 보면", "에 달했다", "상승률", "감소율",
                        "역대 최고", "점유율"],
                  "G": ["연구원 보고서", "보고서에 따르면", "실태조사 결과", "통계에 따르면", "한국은행 자료",
                        "OECD 보고서", "공단 자료", "연구원이 발표한", "백서에 따르면", "국제기구 보고서"],
                  # H: 무작위(아무 기사) 새 세트, I: 인용 장면(공공·연구기관·보고서 이름이 든 문장) 새 세트 — 11/16 새벽, 보고 고치지 않음
                  "H": ["밝혔다", "보면", "기준으로", "달한다", "웃돌았다", "밑돌았다", "차지했다", "불어났다", "급증했다"],
                  "I": ["연구원 분석", "보고서를 보면", "조사 결과를 보면", "통계를 보면", "자료에 따르면", "백서",
                        "공사 자료", "진흥원", "개발원", "위원회 자료"]}[METHOD]
SOURCE_CUE = re.compile(r"따르면|조사|집계|보고서|통계|분석|발표|자료|밝혔|나타났")
# 방식 G: 문장에 공공·연구기관·국제기구·보고서 이름이 있어야 함(인용하려는 숫자 장면)
PUBLIC_CUE = re.compile(r"연구원|연구소|연구센터|보고서|실태조사|백서|통계청|국가데이터처|한국은행|[가-힣]+(부|처|청)(가|는|의|이|에 따르면| 자료)"
                        r"|공단|공사|진흥원|개발원|위원회|OECD|IMF|세계은행|국제[가-힣]+기구|유엔|UN|WHO|IEA|대학교? 연구팀|논문")
# 출처가 아닌 숫자(경기 점수·주가 시세·날씨·사고 소식 등)가 많은 기사는 원문 찾기 대상이 아니므로 제목으로 뺀다
SKIP_TITLE = re.compile(r"\[포토\]|\[사진\]|부고|인사\]|날씨|경기 결과|골|홈런|코스피.*(마감|출발)|환율.*(마감|출발)")


def pick_sentence(paras: list[str]) -> tuple[str, str] | None:
    """(문장, 그 문단). 숫자+단위와 출처 표현이 같이 든 첫 문장."""
    for para in paras:
        for s in (s.strip() for s in re.split(r"(?<=[다요])\.\s+", para)):
            if (MIN_SENT <= len(s) <= MAX_SENT and NUMBER.search(s) and SOURCE_CUE.search(s)
                    and not SKIP_WORDS.search(s) and (METHOD not in ("G", "I") or PUBLIC_CUE.search(s))):
                return s.rstrip(".") + ".", para
    return None


def old_sets() -> tuple[set, set]:
    titles, sents = set(), set()
    for f in (ROOT / "작업").glob("평가*/평가대상.json"):
        if f.parent == OUT_DIR:
            continue
        for t in json.loads(f.read_text(encoding="utf-8")):
            titles.add(t.get("기사제목", "")[:20])
            sents.add(re.sub(r"\s+", "", t.get("문장", "")))
    return titles, sents


def collect_pool(old_titles: set, old_sents: set) -> list[dict]:
    pool, seen_urls, seen_titles = [], set(), set()
    for q in SOURCE_QUERIES:
        got = 0
        for p in range(1, PAGES + 1):
            if got >= MAX_PER_QUERY or len(pool) >= POOL_TARGET:
                break
            try:
                page = fetch_html(DAUM_SEARCH.format(sort=SORT, q=urllib.parse.quote(q), p=p))
            except RuntimeError as e:
                print(f"    검색 결과 받기 실패: '{q}' {p}쪽 — {e}")
                continue
            for url in dict.fromkeys(re.findall(r"https?://v\.daum\.net/v/\w+", page)):
                url = url.replace("http://", "https://")
                if url in seen_urls or got >= MAX_PER_QUERY:
                    continue
                seen_urls.add(url)
                try:
                    art = parse_article(url)
                except RuntimeError as e:
                    print(f"    기사 받기 실패: {url} — {e}")
                    continue
                if not art or SKIP_TITLE.search(art["제목"]):
                    continue
                t20 = art["제목"][:20]
                if t20 in old_titles or t20 in seen_titles:
                    continue
                pick = pick_sentence(art["문단"])
                if not pick or re.sub(r"\s+", "", pick[0]) in old_sents:
                    continue
                seen_titles.add(t20)
                sent, para = pick
                pool.append({"기관": "무작위", "문장": sent, "기사주소": url, "기사제목": art["제목"],
                             "기사날짜": art["날짜"], "검색어": q,
                             "문맥": f"[제목] {art['제목']} [본문] {para[:1200]}"})
                got += 1
        print(f"  검색어 '{q}': 후보 {got}건 (누적 {len(pool)})")
    return pool


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    old_titles, old_sents = old_sets()
    print(f"이미 쓴 평가 세트에서 뺄 기사 제목 {len(old_titles)}개, 문장 {len(old_sents)}개")
    pool = collect_pool(old_titles, old_sents)
    if len(pool) < SAMPLE_SIZE:
        raise SystemExit(f"후보가 {len(pool)}건뿐이라 {SAMPLE_SIZE}건을 뽑을 수 없습니다. PAGES 나 검색어를 늘리세요.")
    (OUT_DIR / "후보전체.json").write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")
    chosen = random.Random(SEED).sample(pool, SAMPLE_SIZE)
    for i, c in enumerate(chosen, start=1):
        c["번호"] = i
    (OUT_DIR / "평가대상.json").write_text(json.dumps(chosen, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n후보 {len(pool)}건 중 {len(chosen)}건 → {OUT_DIR / '평가대상.json'}")
    for c in chosen:
        print(f"  {c['번호']:>2}. ({c['기사날짜']}) {c['문장'][:100]}")


if __name__ == "__main__":
    main()
