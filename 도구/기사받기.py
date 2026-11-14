"""기사 주소(링크)를 받아 제목·날짜·숫자 문장 앞뒤 문단을 채운다(A5, 11/14).

사용자가 문맥을 손으로 붙여 넣지 않아도 되게: 링크만 넣으면 '기사 문맥'과 '기사 날짜'를 자동으로 채운다.
- 제목: og:title → <title>
- 날짜: article:published_time 등 메타 → 본문 첫 'YYYY.MM.DD' 꼴
- 문맥: 본문 글자에서 기사 문장 위치 앞뒤 CONTEXT_CHARS 자(못 찾으면 본문 앞부분)
"""
import html as html_lib
import re
import urllib.parse

from 기관별 import fetch_bytes
from 일반경로 import html_text

CONTEXT_CHARS = 600
HEAD_CHARS = 1200
META = r'<meta[^>]+(?:property|name|itemprop)=["\']{}["\'][^>]+content=["\']([^"\']+)["\']'
DATE_KEYS = ["article:published_time", "og:regDate", "pubdate", "publishdate", "datePublished", "article:published"]
DATE_IN_TEXT = re.compile(r"(20\d{2})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})")


def _meta(page: str, key: str) -> str:
    m = re.search(META.format(re.escape(key)), page, re.I)
    return html_lib.unescape(m.group(1)).strip() if m else ""


CLUE_WORDS = re.compile(r"따르면|연구진|연구팀|연구 결과|논문|학술지|저널|게재|발표|보고서|공시|보도자료|"
                        r"조사|밝혔|대학|연구소|연구원|기관|협회|NBER|Journal|Inc|Corp")
MAX_CLUES, CLUE_CHARS = 3, 600


def source_clues(flat: str, sentence: str) -> str:
    """기사 전체에서 출처(기관·논문·회사)를 밝힌 문장 몇 개. 기사 문장에 기관 이름이 없을 때 대비(11/14 무작위3:
    '1.13%포인트' 문장엔 기관이 없고 앞 문단에 NBER 논문이 나옴). 문맥 칸 끝에 '[출처 단서]'로 붙인다."""
    head = re.sub(r"\s+", " ", sentence)[:20]
    out, size = [], 0
    for s in re.split(r"(?<=다)\.\s+|(?<=[.!?])\s+(?=[A-Z가-힣])", flat):
        s = s.strip()
        if not (20 <= len(s) <= 300) or head in s or not CLUE_WORDS.search(s):
            continue
        if size + len(s) > CLUE_CHARS:
            break
        out.append(s)
        size += len(s)
        if len(out) >= MAX_CLUES:
            break
    return (" [출처 단서] " + " / ".join(out)) if out else ""


def valid_url(url: str) -> bool:
    p = urllib.parse.urlparse(url or "")
    return p.scheme in ("http", "https") and bool(p.netloc)


def fetch_article(url: str, sentence: str = "") -> dict:
    """{'제목', '날짜'(YYYY-MM-DD 또는 ''), '문맥'}. 받기 실패면 RuntimeError(한국어 이유)."""
    if not valid_url(url):
        raise RuntimeError("기사 주소는 http:// 또는 https:// 로 시작해야 합니다.")
    raw = fetch_bytes(url)
    page = raw.decode("utf-8", "replace")
    if "�" in page[:3000] and re.search(rb"charset=[\"']?(euc-kr|ks_c_5601)", raw[:3000], re.I):
        page = raw.decode("cp949", "replace")   # 옛 언론사 누리집(EUC-KR)
    title = _meta(page, "og:title") or (re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I) or [None, ""])[1]
    title = re.sub(r"\s+", " ", html_lib.unescape(title)).strip()
    date = ""
    for key in DATE_KEYS:
        if m := DATE_IN_TEXT.search(_meta(page, key)):
            date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            break
    text = html_text(page.encode("utf-8"))
    if not date and (m := DATE_IN_TEXT.search(text)):
        date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    flat = re.sub(r"\s+", " ", text)
    pos = flat.find(re.sub(r"\s+", " ", sentence)[:20]) if sentence else -1
    body = flat[max(0, pos - CONTEXT_CHARS): pos + CONTEXT_CHARS] if pos >= 0 else flat[:HEAD_CHARS]
    if len(body) < 50:
        raise RuntimeError("기사 본문을 읽지 못했습니다(자바스크립트로만 보이는 누리집일 수 있음). 문맥을 직접 붙여 넣어 주세요.")
    clues = source_clues(flat, sentence) if sentence else ""
    return {"제목": title, "날짜": date, "문맥": f"[제목] {title} [본문] {body}{clues}", "문장위치찾음": pos >= 0}
