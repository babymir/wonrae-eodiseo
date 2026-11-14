"""평가용 실제 기사 문장 모으기 → 기관별 무작위 N건 뽑기.

1) 라이너 웹 검색(최근 1년)으로 기관별 기사 주소를 모은다.
2) 기사 본문을 받아 '기관 이름 + 숫자'가 함께 든 문장을 뽑는다(기사당 1문장, 첫 문장).
3) 고정 시드로 기관별 SAMPLE_PER_INST 건을 무작위로 뽑아 저장한다.

사용: python3 도구/평가_기사모으기.py
결과: 작업/평가/기사후보.json (전체 후보), 작업/평가/평가대상.json (뽑힌 30건)
중간 저장: 받은 기사는 작업/평가/기사캐시/ 에 저장해 다시 받지 않는다.
"""
import hashlib
import html as html_lib
import json
import random
import re
from pathlib import Path

from liner_api import ask_json, web_search
from 기관별 import fetch_bytes

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "작업" / "평가"
CACHE_DIR = OUT_DIR / "기사캐시"
SEED = 20261112
SAMPLE_PER_INST = 10
MIN_SENT, MAX_SENT = 25, 260  # 문장 길이(글자)

# 표본 틀(평가표에 밝힘). 일반 문구('발표','분석')로는 기관 자체 소식(정책·인사)만 나와서,
# 연구원은 '보고서 인용 문구·간행물 이름', 국가데이터처는 '정기 통계 이름'으로 기사를 찾는다.
DATE_RANGES = ["past_week", "past_month", "past_year"]
MODS_STATS = ["고용동향", "소비자물가동향", "인구동향", "가계동향", "산업활동동향",
              "사회조사", "경제활동인구조사", "가계금융복지조사", "온라인쇼핑동향", "주택소유통계"]
QUERIES = {
    "자본시장연구원": ["자본시장연구원 보고서에 따르면", "자본시장연구원 연구위원은", "자본연 보고서",
                  "자본시장연구원 이슈보고서", "자본시장연구원 자본시장포커스"],
    "국가데이터처": [f"국가데이터처 {s}" for s in MODS_STATS],
    "한국보건사회연구원": ["한국보건사회연구원 보고서에 따르면", "한국보건사회연구원 연구위원은", "보사연 보고서",
                   "보건복지 이슈앤포커스", "보건복지포럼 보고서"],
}
NAMES = {
    "자본시장연구원": ["자본시장연구원"],
    "국가데이터처": ["국가데이터처", "통계청"],
    "한국보건사회연구원": ["한국보건사회연구원", "보건사회연구원", "보사연"],
}
# 기관 누리집·사본·SNS 는 '기사'가 아님
SKIP_HOSTS = re.compile(r"\.go\.kr|\.re\.kr|\.ac\.kr|\.or\.kr|kosis\.kr|korea\.kr|kihasa|youtube|instagram|x\.com|twitter|facebook|blog|cafe|"
                        r"tistory|brunch|namu\.wiki|wikipedia|linkedin|threads")
NUMBER = re.compile(r"\d[\d,.]*\s*(%|%p|%포인트|만\s*\d*\s*천?\s*명|만|천|억|조|명|원|배|곳|건|가구)")


def fetch_article(url: str) -> str | None:
    """기사 본문 글자(태그 제거). 실패하면 None (실패 이유는 출력)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / (hashlib.md5(url.encode()).hexdigest() + ".txt")
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    try:
        raw = fetch_bytes(url)
    except Exception as e:  # 기사 하나 실패는 건너뛰고 계속
        print(f"    기사 받기 실패: {url} — {e}")
        return None
    charset = re.search(rb'charset=["\']?([\w-]+)', raw[:3000])
    enc = charset.group(1).decode() if charset else "utf-8"
    try:
        text = raw.decode(enc, "replace")
    except LookupError:
        text = raw.decode("utf-8", "replace")
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"[ \t\r\xa0]+", " ", text)
    cache.write_text(text, encoding="utf-8")
    return text


PICK_PROMPT = """아래는 뉴스 기사 일부다. '{inst}'(옛 이름 포함)가 낸 보고서·통계·조사 결과의 숫자를
인용한 문장을 기사에서 그대로 하나 골라라. 숫자는 연구·통계 결과여야 한다
(포상금, 예산, 행사 날짜, 조사 일정, 인원 채용 같은 행정 소식은 제외. 기사 제목 줄도 제외).
기관 이름이 그 문장에 없어도 앞뒤 문맥상 그 기관 결과면 된다. 없으면 "문장": "".
JSON 하나로만 답하라: {{"문장": "기사에 있는 그대로의 한 문장"}}
기사 일부:
{text}"""
WINDOW_CHARS = 500
MAX_TEXT_CHARS = 3000


def pick_sentence(text: str, inst: str, names: list[str]) -> str | None:
    """라이너 모델이 고른 '기관 숫자 인용 문장'. 기사 본문에 그대로 있는 문장만 받아들인다."""
    spans = [(max(0, m.start() - WINDOW_CHARS), m.end() + WINDOW_CHARS)
             for n in names for m in re.finditer(re.escape(n), text)]
    if not spans:
        return None
    spans.sort()
    parts, last_end = [], -1
    for s, e in spans:
        if s > last_end:
            parts.append(text[s:e])
            last_end = e
    excerpt = re.sub(r"\s+", " ", " … ".join(parts))[:MAX_TEXT_CHARS]
    try:
        sent = (ask_json(PICK_PROMPT.format(inst=inst, text=excerpt)).get("문장") or "").strip()
    except Exception as e:
        print(f"    모델 문장 고르기 실패: {e}")
        return None
    if not (MIN_SENT <= len(sent) <= MAX_SENT) or not NUMBER.search(sent):
        return None
    if re.sub(r"\s+", "", sent) not in re.sub(r"\s+", "", text):  # 지어낸 문장 차단
        print(f"    기사에 없는 문장이라 버림: {sent[:50]}")
        return None
    return sent


def collect() -> list[dict]:
    pool, seen_urls = [], set()
    for inst, queries in QUERIES.items():
        for q, dr in [(q, dr) for q in queries for dr in DATE_RANGES]:
            results = web_search(q, max_results=20, date_range=dr)
            before = len(pool)
            for r in results:
                url = r.get("url", "")
                host = r.get("hostname", "")
                if not url or url in seen_urls or SKIP_HOSTS.search(host + url):
                    continue
                seen_urls.add(url)
                text = fetch_article(url)
                pick_cache = CACHE_DIR / (hashlib.md5(f"{inst}|{url}".encode()).hexdigest() + ".pick.json")
                if pick_cache.exists():  # 중간 저장: 이미 고른 기사는 모델에 다시 묻지 않음
                    sent = json.loads(pick_cache.read_text(encoding="utf-8"))["문장"]
                else:
                    sent = pick_sentence(text, inst, NAMES[inst]) if text else None
                    pick_cache.write_text(json.dumps({"문장": sent}, ensure_ascii=False), encoding="utf-8")
                if sent:
                    pool.append({"기관": inst, "문장": sent, "기사주소": url,
                                 "기사제목": r.get("title", ""), "기사날짜": r.get("date")})
            print(f"[{inst}] '{q}' ({dr}) → 검색 {len(results)}건, 문장 {len(pool) - before}건")
    return pool


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pool = collect()
    (OUT_DIR / "기사후보.json").write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")
    rng = random.Random(SEED)
    chosen, leftovers = [], []
    for inst in QUERIES:
        group, seen_numbers = [], set()
        for p in pool:  # 같은 숫자 묶음의 문장(같은 기사 재전송)은 한 건만
            nums = tuple(sorted(set(re.findall(r"\d+(?:\.\d+)?", p["문장"]))))
            if p["기관"] == inst and nums not in seen_numbers:
                seen_numbers.add(nums)
                group.append(p)
        rng.shuffle(group)
        if len(group) < SAMPLE_PER_INST:
            print(f"⚠ {inst}: 후보 {len(group)}건뿐 (목표 {SAMPLE_PER_INST}건)")
        chosen += group[:SAMPLE_PER_INST]
        leftovers += group[SAMPLE_PER_INST:]
    # 모자란 기관 몫은 다른 기관의 남은 후보에서 무작위로 채운다(평가표에 '보충'으로 표시)
    shortfall = SAMPLE_PER_INST * len(QUERIES) - len(chosen)
    rng.shuffle(leftovers)
    for extra in leftovers[:max(0, shortfall)]:
        extra["보충"] = True
        chosen.append(extra)
    if shortfall > 0:
        print(f"모자란 {shortfall}건은 다른 기관 남은 후보로 보충")
    for i, c in enumerate(chosen, start=1):
        c["번호"] = i
    (OUT_DIR / "평가대상.json").write_text(json.dumps(chosen, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n후보 {len(pool)}건 중 {len(chosen)}건 선택 → {OUT_DIR / '평가대상.json'}")
    for c in chosen:
        print(f"  {c['번호']:>2}. [{c['기관']}] {c['문장'][:90]}")


if __name__ == "__main__":
    main()
