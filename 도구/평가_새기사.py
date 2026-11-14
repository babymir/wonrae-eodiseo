"""새 기사 평가 세트 만들기 — 도구(라이너 검색·모델)와 독립된 경로로 고른다.

- 기사 찾기: 다음(Daum) 뉴스 검색, 최신순 (라이너 검색 아님)
- 문장 고르기: 규칙만 사용 (라이너 모델 아님)
  기관 이름이 나온 문장, 또는 그 바로 다음 문장 중 '숫자+단위'가 든 첫 문장.
  행정 소식(포상·인사·행사 등) 문장은 뺀다.
- 이전 평가(작업/평가/평가대상.json)와 같은 기사 제목은 뺀다.

사용: python3 도구/평가_새기사.py [결과 폴더] [정렬: recency|accuracy]
      예) python3 도구/평가_새기사.py 작업/평가_새기사3 accuracy   (11/13 세 번째 세트: 처음 보는 문장)
결과: <결과 폴더>/평가대상.json  → python3 도구/평가_실행.py <결과 폴더>
이미 만든 평가 세트(작업/평가*/평가대상.json)와 같은 기사 제목·같은 문장은 모두 뺀다.
중간 저장: 받은 기사는 작업/평가_새기사/기사캐시/ 에 저장해 다시 받지 않는다.
"""
import hashlib
import html as html_lib
import json
import re
import sys
import urllib.parse
from pathlib import Path

from 기관별 import fetch_bytes

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "작업/평가_새기사")
SORT = sys.argv[2] if len(sys.argv) > 2 else "recency"
CACHE_DIR = OUT_DIR / "기사캐시"
DAUM_SEARCH = "https://search.daum.net/search?w=news&sort={sort}&q={q}&p={p}"
PAGES = 10                # 검색어마다 볼 검색 결과 쪽 수(쪽당 기사 약 5개)
PER_INST = 7              # 기관마다 뽑을 문장 수
MIN_SENT, MAX_SENT = 25, 300

INSTS = {
    "자본시장연구원": ["자본시장연구원", "자본연"],
    "국가데이터처": ["국가데이터처", "데이터처", "통계청"],
    "한국보건사회연구원": ["한국보건사회연구원", "보건사회연구원", "보사연"],
}
NUMBER = re.compile(r"\d[\d,.]*\s*(%p|%포인트|%|만\s*\d*\s*천?\s*명|만\s*명|천\s*명|명|조\s*\d*\s*억?\s*원|억\s*원|만\s*원|조원|억원|만원|원|배|곳|건|가구|달러)")
SKIP_WORDS = re.compile(r"포상|채용|인사|개최|행사|워크숍|간담회|세미나|업무협약|MOU|기념식|취임|임명|부고|발령|"
                        r"참석|대표단|단장|선정|예산안|공동연구를 수행")


def number_key(inst: str, sent: str) -> str:
    """같은 내용을 표현만 바꾼 문장(통신사 재전송·다른 기사) 거르기: 기관 + 문장 속 숫자 묶음."""
    return inst + "|" + ",".join(sorted(m.group(0).replace(" ", "") for m in NUMBER.finditer(sent)))


def fetch_html(url: str) -> str:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / (hashlib.md5(url.encode()).hexdigest() + ".html")
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    text = fetch_bytes(url).decode("utf-8", "replace")
    cache.write_text(text, encoding="utf-8")
    return text


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parse_article(url: str) -> dict | None:
    h = fetch_html(url)
    title = re.search(r"<title>(.*?)</title>", h, re.S)
    date = re.search(r'<span class="num_date">(.*?)</span>', h)
    paras = [clean(p) for p in re.findall(r'<p[^>]*dmcf-ptype="general"[^>]*>(.*?)</p>', h, re.S)]
    paras = [p for p in paras if p]
    if not title or not paras:
        return None
    d = re.search(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", date.group(1)) if date else None
    return {"주소": url, "제목": clean(title.group(1)).split(" | ")[0],
            "날짜": f"{d.group(1)}-{int(d.group(2)):02d}-{int(d.group(3)):02d}" if d else "", "문단": paras}


def pick_sentence(paras: list[str], names: list[str]) -> tuple[str, str] | None:
    """(문장, 그 문단). 기관 이름이 나온 문장 또는 그 다음 문장 중 숫자+단위가 든 첫 문장."""
    for para in paras:
        sents = [s.strip() for s in re.split(r"(?<=[다요])\.\s+", para) if s.strip()]
        for i, s in enumerate(sents):
            if not any(n in s for n in names):
                continue
            for cand in sents[i:i + 2]:
                if MIN_SENT <= len(cand) <= MAX_SENT and NUMBER.search(cand) and not SKIP_WORDS.search(cand):
                    return cand.rstrip(".") + ".", para
    return None


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    old_titles, old_sents = set(), set()
    for f in (ROOT / "작업").glob("평가*/평가대상.json"):
        if f.parent == OUT_DIR:
            continue
        for t in json.loads(f.read_text(encoding="utf-8")):
            old_titles.add(t.get("기사제목", "")[:20])
            old_sents.add(re.sub(r"\s+", "", t.get("문장", "")))
            old_sents.add(number_key(t.get("기관", ""), t.get("문장", "")))
    print(f"이미 쓴 평가 세트에서 뺄 기사 제목 {len(old_titles)}개, 문장 {len(old_sents)}개")
    chosen = []
    for inst, names in INSTS.items():
        got, seen, seen_sents = [], set(), set()
        for q, p in [(q, p) for q in (names[0], f"{names[0]} 보고서") for p in range(1, PAGES + 1)]:
            if len(got) >= PER_INST:
                break
            page = fetch_html(DAUM_SEARCH.format(sort=SORT, q=urllib.parse.quote(q), p=p))
            for url in dict.fromkeys(re.findall(r"https?://v\.daum\.net/v/\w+", page)):
                url = url.replace("http://", "https://")
                if url in seen or len(got) >= PER_INST:
                    continue
                seen.add(url)
                try:
                    art = parse_article(url)
                except RuntimeError as e:
                    print(f"    기사 받기 실패: {url} — {e}")
                    continue
                if not art or art["제목"][:20] in old_titles:
                    continue
                pick = pick_sentence(art["문단"], names)
                if not pick:
                    continue
                sent, para = pick
                keys = {re.sub(r"\s+", "", sent), number_key(inst, sent)}
                if keys & (seen_sents | old_sents):  # 같은 문장·같은 숫자 묶음(재전송·이전 세트)은 빼기
                    continue
                seen_sents |= keys
                got.append({"기관": inst, "문장": sent, "기사주소": url, "기사제목": art["제목"],
                            "기사날짜": art["날짜"], "문맥": f"[제목] {art['제목']} [본문] {para[:1200]}"})
        print(f"[{inst}] {len(got)}건")
        chosen += got
    for i, c in enumerate(chosen, start=1):
        c["번호"] = i
    (OUT_DIR / "평가대상.json").write_text(json.dumps(chosen, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(chosen)}건 → {OUT_DIR / '평가대상.json'}")
    for c in chosen:
        print(f"  {c['번호']:>2}. [{c['기관']}] ({c['기사날짜']}) {c['문장'][:90]}")


if __name__ == "__main__":
    main()
