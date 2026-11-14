"""원 보고서 후보 PDF 를 받아 글자를 뽑고, '기사가 말한 보고서'일 가능성으로 줄 세운다.

- 기준 시점 일치(데이터처 월·분기 정기통계는 엄격히), 기사가 밝힌 보고서 이름(제목만·핵심 낱말까지 같아야 맨 앞),
  라이너 Rerank 제목 비슷함, 지역판·보도자료는 뒤로
- 걸러 내는 것: 기관의 보도 설명자료·해명 문서(기사 문장을 인용하므로 원문이 아님)
- 정기간행물 묶음 표지(1쪽 목차)면 2쪽 큰 글씨를 글 제목으로
"""
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from PDF도구 import (
    download_pdf,
    layout_pages,
    pdf_pages,
    period_score,
    periodical_article_title,
    title_similarity,
)
import 일반경로
from 기관별 import Candidate
from 순위 import rerank_scores

MAX_DOWNLOAD = 8          # 제목 순위 상위 몇 개 PDF 만 받을지(11/14: 6 → 8, 전용 출처 우선 후보가 자리를 차지)


COVER_CHARS = 400         # 제목 비교에 쓸 1쪽 첫머리 글자 수


WORKERS = 6


@dataclass
class Doc:
    cand: Candidate
    pdf: Path
    pages: list[str]
    layout: list[str]
    period: int
    title_sim: float      # 라이너 Rerank 가 덮어쓸 수 있는 '제목 비슷함'
    name_sim: float = 0.0  # 기사가 밝힌 보고서 이름과의 글자 묶음 일치(Rerank 가 덮어쓰지 않음)
    missing: int = 0       # 기사가 밝힌 이름의 핵심 낱말 중 이 후보 제목·표지에 없는 개수
    cover: str = ""        # 제목 비교에 쓰는 표지 첫머리(정기간행물 묶음 표지면 2쪽)


def article_day(article_date: str) -> date | None:
    m = re.search(r"(20\d{2})\D{0,3}(\d{1,2})\D{0,3}(\d{1,2})", article_date or "")
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
    except ValueError:
        return None


EDITION_WORDS = re.compile(r"20\d{2}\s*년?|\d{1,2}\s*월|\d\s*/\s*4\s*분기|\d\s*분기|[상하]반기|\(첨부 \d+\)|[|·].*$|\s+")


def _edition_year(doc: "Doc") -> int:
    m = re.search(r"(20\d{2})\s*년", doc.cand.title) or re.search(r"(20\d{2})\s*년", doc.pages[0][:COVER_CHARS] if doc.pages else "")
    return int(m.group(1)) if m else 0


def latest_editions(docs: list["Doc"], article_date: str) -> list["Doc"]:
    """기사에 기준 시점이 없을 때: 같은 제목의 해마다 판(2022·2026년 부가조사 등)은
    기사 날짜(없으면 오늘) 이전의 가장 최근 판만 남긴다(11/13: 194만원이 2022년 판을 고른 문제)."""
    limit = (article_day(article_date) or date.today()).year
    groups: dict[str, list[Doc]] = {}
    for d in docs:
        groups.setdefault(EDITION_WORDS.sub("", d.cand.title), []).append(d)
    keep = []
    for same in groups.values():
        dated = [d for d in same if 0 < _edition_year(d) <= limit]
        if len(same) > 1 and dated:
            newest = max(_edition_year(d) for d in dated)
            dropped = [d for d in same if _edition_year(d) != newest]
            if dropped:
                print(f"    같은 제목의 다른 해 판 {len(dropped)}건은 빼고 {newest}년 판을 씀(기사 날짜 기준 최신)")
            keep += [d for d in same if _edition_year(d) == newest]
        else:
            keep += same
    return keep


REGIONS = ["서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "강원", "충북", "충남", "충청",
           "전북", "전남", "전라", "경북", "경남", "경상", "제주",
           # 여러 지역을 묶은 지역판(11/14 회귀: '수도권 시도별 산업활동동향'을 전국판보다 앞에 둬 '원문에 없음')
           "수도권", "시도별", "시·도별", "광역시", "특별시", "시군별"]


def region_penalty(title: str, article: str) -> int:
    """지역판 보도자료 거르기: 제목에 지역 이름이 있는데 기사에는 그 지역이 없으면 1(뒤로).
    (11/13: '9월 산업활동동향 3.0%'에 '강원 산업활동동향'을 고른 문제. 기사에 '제주'가 있으면 제주판이 앞으로)"""
    head = re.split(r"[|]", title)[0]
    named = [r for r in REGIONS if r in head]
    return 1 if named and not any(r in article for r in named) else 0


def _is_press(doc: "Doc") -> bool:
    """보사연 보도자료(짧은 요약). 같은 숫자가 원 보고서에도 있으면 원 보고서를 앞에 둔다."""
    return "/news/press/" in doc.cand.page_url or (doc.cand.file_key.startswith("kihasa") and len(doc.pages) <= 4)


def _load_one(c: Candidate, tokens: dict, report_name: str) -> Doc | None:
    try:  # 일반 경로 후보(gen_)는 PDF 가 아니면 웹 페이지 글자로 받는다
        pdf = 일반경로.fetch_doc(c) if c.file_key.startswith("gen_") else download_pdf(c)
    except RuntimeError as e:  # 후보 하나 실패는 건너뜀(이유 출력)
        print(f"    받기 실패: {e}")
        return None
    if pdf is None:
        return None
    try:
        pages = 일반경로.web_pages(pdf) if 일반경로.is_web(pdf) else pdf_pages(pdf)
    except RuntimeError as e:  # 글자 추출 실패·시간 초과: 이 후보만 건너뜀
        print(f"    {e}")
        return None
    if pages and REBUTTAL_TEXT.search(squash_head(pages[0])):
        # 기관이 기사를 해명한 '보도 설명자료'는 기사 문장을 그대로 인용한다 → 원 보고서로 쓰면 틀린 기사를 '일치'라 함
        # (제목이 잘려 제목 필터를 빠져나간 경우까지 내용으로 거름 — 11/13 오보 사례 #2)
        print(f"    보도 설명자료라 후보에서 뺌: {c.title[:40]}")
        return None
    layout = [] if 일반경로.is_web(pdf) else layout_pages(pdf)
    cover = pages[0][:COVER_CHARS] if pages else ""
    if pages and (len(c.title.strip()) < 4 or c.title.strip() in ("다운로드", "첨부파일", "download")):
        first = next((ln.strip() for ln in pages[0].splitlines() if len(ln.strip()) >= 6), "")
        if first:  # 누리집 페이지에서 제목을 못 읽은 후보: PDF 첫머리 줄로(화면에 '다운로드'로 나오던 것)
            c = replace(c, title=first[:80])
    article = "" if 일반경로.is_web(pdf) else periodical_article_title(pdf)
    if article:  # 정기간행물 묶음 표지 PDF: 실제 글 제목으로 바꾸고, 표지(다른 글 제목 목록)는 비교에서 뺀다
        c = replace(c, title=f"{article} ({c.title})")
        cover = pages[1][:COVER_CHARS] if len(pages) > 1 else ""
    # 제목 비교는 후보 제목 + 표지 첫머리만. 긴 본문까지 넣으면 웬만한 글자가 다 들어 있어 변별력이 없음
    sim = title_similarity(report_name, c.title + " " + cover)
    # '이름이 같은 후보'는 후보 제목만으로, 핵심 낱말이 하나도 빠지지 않을 때만(표지 글까지 넣으면 비슷한 주제의
    # 다른 보고서도 0.9를 넘음 — 11/13 기존 #2 '2050 탄소중립을 위한 자본시장의 변화와 발전 과제' 0.93)
    name_sim = (title_similarity(report_name, c.title)
                if report_name and missing_nouns(report_name, c.title) == 0 else 0.0)
    doc = Doc(c, pdf, pages, layout, period_score(pages, c.title, tokens), sim, name_sim,
              missing_nouns(report_name, c.title + " " + cover) if report_name else 0, cover)
    print(f"    후보: {c.title[:40]} — {pdf.name}, {len(pages)}쪽, "
          f"기준시점 일치 {doc.period}/{len(tokens)}, 제목 비슷함 {sim:.2f}")
    return doc


REBUTTAL_TEXT = re.compile(r"보도설명자료|보도해명자료|설명자료|해명자료|언론보도내용")


def squash_head(page: str) -> str:
    return re.sub(r"\s", "", page[:600])


NAMED_TITLE_MIN = 0.9  # 기사가 밝힌 보고서 이름과 이만큼 같으면 Rerank 점수와 상관없이 맨 앞


TITLE_STOP = {"위한", "위해", "현황", "과제", "방안", "정책", "정책과제", "분석", "연구", "보고서", "발전", "변화", "대응",
              "특성", "중심으로", "년도", "실태", "결과", "조사", "동향", "시사점", "이슈", "대한", "관한", "통한", "효과", "개선"}


JOSA = re.compile(r"(으로|에서|에게|과의|와의|을|를|의|와|과|에|은|는|이|가|로|및)$")


def key_nouns(report_name: str) -> list[str]:
    """기사가 밝힌 보고서 이름의 핵심 낱말(흔한 말 빼고). '탄소중립을 위한 기후금융 발전 과제' → [탄소중립, 기후금융]"""
    out = []
    for w in re.findall(r"[가-힣A-Za-z]{2,}", report_name or ""):
        w = JOSA.sub("", w)
        if len(w) >= 2 and w not in TITLE_STOP:
            out.append(w)
    return out


def missing_nouns(report_name: str, title_and_cover: str) -> int:
    """핵심 낱말 중 후보 제목·표지에 없는 개수(띄어쓰기 무시). 부정 신호로 쓴다(Crossref 검증 단계와 같은 생각)."""
    text = re.sub(r"\s", "", title_and_cover)
    return sum(1 for w in key_nouns(report_name) if w not in text)


def named(d: Doc) -> int:
    """기사가 보고서 이름을 밝혔고 이 후보 제목·표지가 그 이름과 거의 같은지(1/0).
    11/13: 이름이 같은 후보(제목 비슷함 1.00)가 있는데 Rerank 가 비슷한 주제의 다른 보고서를 1위로 올림
    (새 기사 #5 '애널리스트의 낙관성, 정확성, 정보성' → '애널리스트의 낙관적 편향', 처음 보는 #15)."""
    return 1 if d.name_sim >= NAMED_TITLE_MIN else 0


def load_docs(cands: list[Candidate], tokens: dict, report_name: str, query: str,
              article_date: str = "", article: str = "", keep: set[str] | None = None) -> tuple[list[Doc], str]:
    """후보 PDF 를 동시에 받아 '기준 시점 일치 → 보고서 제목 비슷한 정도' 순으로 줄 세운다.
    제목 비슷한 정도: 라이너 Rerank 점수(query ↔ 후보 제목·표지). 실패하면 두 글자 묶음 비교.
    받기 전에 후보 제목으로 먼저 순위를 매겨 앞의 MAX_DOWNLOAD 개만 받는다.
    keep: 꼭 받을 후보의 file_key(발간 목록에서 찾은 후보). 줄일 때 빠지지 않게 앞에 둔다.
    돌려주는 값: (문서 목록, 비었을 때 이유)"""
    import 일반경로   # 순환 import 피하려고 여기서
    pre = (rerank_scores(query, [f"{c.title} {일반경로.snippet_of(c.page_url)[:300]}" for c in cands])
           if len(cands) > MAX_DOWNLOAD else None)
    if pre:
        ranked = [c for _, c in sorted(zip(pre, cands), key=lambda x: -x[0])]
    else:
        ranked = sorted(cands, key=lambda c: -title_similarity(report_name, c.title))
    if not tokens:
        # 기준 시점이 없는 문장: 같은 제목의 해마다 판이 많으면 6개로 줄일 때 최신 판이 빠질 수 있다
        # → 제목(연도 뺀)이 같은 묶음에서는 기사 날짜 이전의 가장 최근 판을 먼저 받는다
        limit = (article_day(article_date) or date.today()).year
        newest: dict[str, Candidate] = {}
        for c in ranked:
            m = re.search(r"(20\d{2})\s*년", c.title)
            year = int(m.group(1)) if m else 0
            key = EDITION_WORDS.sub("", c.title)
            old = newest.get(key)
            old_year = int(re.search(r"(20\d{2})", old.title).group(1)) if old and re.search(r"(20\d{2})", old.title) else 0
            if year <= limit and (old is None or year > old_year):
                newest[key] = c
        first = [c for c in ranked if c in newest.values()]
        ranked = first + [c for c in ranked if c not in first]
    ranked.sort(key=lambda c: region_penalty(c.title, article))   # 안정 정렬: 순서는 유지하고 지역판만 뒤로
    if keep:
        ranked.sort(key=lambda c: c.file_key not in keep)          # 발간 목록 후보는 반드시 받는다
    cands = ranked[:MAX_DOWNLOAD]
    with ThreadPoolExecutor(WORKERS) as ex:
        docs = [d for d in ex.map(lambda c: _load_one(c, tokens, report_name), cands) if d]
    if len(docs) > 1:  # 라이너 Rerank 로 '제목 비슷함'을 다시 매긴다(실패하면 글자 비교 점수 유지)
        covers = [d.cand.title + " " + d.cover for d in docs]
        scores = rerank_scores(query, covers)
        if scores:
            for d, s in zip(docs, scores):
                d.title_sim = s
            print(f"    라이너 Rerank 로 후보 보고서 {len(docs)}건 순위 매김")
    monthly = docs and all(d.cand.file_key.startswith("mods_") for d in docs[:1])
    # 엄격한 시점 필터는 국가데이터처 월·분기 정기통계에만(연구원 보고서는 발간 시점과 자료 시점이 다름 —
    # 11/13 회귀: '8월 이후'에 연도를 채우자 자본연 보고서까지 '시점 안 맞음'으로 버림)
    if monthly and ("월" in tokens or "분기" in tokens):
        # 월·분기 정기 통계(고용동향 등): 기준 시점(년·월/분기)이 모두 맞는 판만 남긴다.
        # 다 맞는 판이 없으면 버린다 — 다른 달 판으로 '원문에 없음'이라 하는 것보다 정직(11/12 새 기사 #6)
        full = [d for d in docs if d.period == len(tokens)]
        if not full:
            print(f"    기준 시점({'·'.join(tokens.values())})이 모두 맞는 판이 없어 후보를 버림")
            return [], "기사가 말한 기준 시점의 보도자료를 찾지 못함(다른 달·분기 판만 찾음)"
        docs = sorted(full, key=lambda d: (region_penalty(d.cand.title, article), -named(d), d.missing, -d.title_sim))
    else:
        # 연구보고서: 자료 연도(2024)와 발간 연도가 달라 엄격히 거르면 맞는 보고서가 빠짐 → 제목 위주로 합산
        if not tokens:
            docs = latest_editions(docs, article_date)
        has_report = any(not _is_press(d) for d in docs)
        docs.sort(key=lambda d: (region_penalty(d.cand.title, article), -named(d), d.missing,
                                 -(d.title_sim * 2 + d.period - (1 if has_report and _is_press(d) else 0))))
    return docs, "" if docs else "원 보고서 PDF를 찾지 못함"
