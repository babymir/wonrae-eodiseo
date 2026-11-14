"""일반 경로 — 기관을 가리지 않고 원문 후보(PDF·웹 페이지)를 찾는다. 이것이 기본 길이다.

세 기관(자본연·데이터처·보사연)의 전용 코드(기관별.py, 발간목록.py)는 그 기관일 때 더 확실하게 받는 보조 장치일 뿐.
(11/14 무작위 30건: 세 기관 인용 0건, 출처는 해외 통계청·다른 공공기관·민간 집계·논문으로 흩어져 있음)

1. 라이너 모델이 짚은 기관 공식 누리집(기관누리집)에서 site: 검색 — PDF 와 웹 페이지 모두
2. 해외 기관이면 원어 검색어로 한 번 더(lang=en)
3. 공공(go.kr)·연구(re.kr)·공공법인(or.kr) 누리집 PDF 일반 검색(기관 누리집을 모를 때 대비)
후보는 '기관 누리집 안의 문서'를 앞에, 언론·블로그는 뺀다. 받은 문서가 PDF 가 아니면 웹 페이지 글자로 대조한다.
"""
import hashlib
import html as html_lib
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import 문서읽기
from 숫자표기 import to_regex, variants
from liner_api import web_search
from 기관별 import Candidate, fetch_bytes

WEB_DIR = Path(__file__).resolve().parent.parent / "작업" / "웹문서"
PDF_DIR = Path(__file__).resolve().parent.parent / "작업" / "원본PDF"
MAX_CANDIDATES = 20   # 받기 전 순위(제목+검색 요약 Rerank)로 다시 줄인다(후보문서.MAX_DOWNLOAD). 11/14: 8개로 자르면 검색 순서 뒤의 정답이 빠짐
WEB_PAGE_CHARS = 3000     # 웹 페이지 글자를 이 길이로 나눠 '쪽'처럼 다룬다(화면에는 '웹 페이지 n번째 부분')
MIN_WEB_CHARS = 200       # 이보다 짧은 페이지(빈 화면·자바스크립트만)는 버린다
DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
PUBLIC_PDF = re.compile(r"^https?://[^/]+\.(?:go|re|or)\.kr/\S*(?:\.pdf|[Dd]own|fileDown|download|atchFile)")
NEWS_OR_SOCIAL = re.compile(r"news|daum\.net|naver\.com|instagram|facebook|youtube|x\.com|twitter|blog|tistory|"
                            r"threads|wikipedia|namu\.wiki|reddit|medium\.com|"
                            # 국내 주요 언론(기사는 원문이 아님 — 후보 자리를 차지하지 않게, 11/14)
                            r"yna\.co\.kr|chosun\.com|joongang\.co\.kr|donga\.com|hani\.co\.kr|khan\.co\.kr|"
                            r"hankyung\.com|mk\.co\.kr|mt\.co\.kr|edaily\.co\.kr|sedaily\.com|asiae\.co\.kr|"
                            r"heraldcorp\.com|fnnews\.com|etnews\.com|zdnet\.co\.kr|ohmynews|pressian|kbs\.co\.kr|"
                            r"imbc\.com|sbs\.co\.kr|ytn\.co\.kr|jtbc|hankookilbo|seoul\.co\.kr|segye\.com|kmib\.co\.kr|"
                            r"munhwa\.com|bizwatch|dt\.co\.kr|inews24|ajunews|thebell|kyeonggi\.com|kgnews|asiatime")
# 공공기관 주소는 'news'가 들어가도 언론이 아니다(11/14: 경기도 보도자료 gnews.gg.go.kr 를 언론으로 걸러 놓침)
PUBLIC_HOST = re.compile(r"\.go\.kr$|\.gov(?:\.[a-z]{2})?$|\.re\.kr$|\.ac\.kr$|\.mil$|\.int$|\.gouv\.fr$|\.go\.jp$")


def is_news_or_social(url: str, domain: str = "") -> bool:
    h = host(url)
    if PUBLIC_HOST.search(h) or (domain and on_domain(url, domain)):
        return False
    return bool(NEWS_OR_SOCIAL.search(h))


SNIPPET_MARK = "[라이너 검색 요약]"   # 자동 접속이 막힌 누리집: 검색 결과에 붙은 그 페이지 요약 글로 대조(화면에 따로 표시)
_snippets: dict[str, str] = {}       # 주소 → 라이너 검색 결과 요약 글(기관 누리집 결과만)
EN_STOP = {"the", "and", "for", "with", "from", "data", "report", "release", "news", "of", "in", "on", "to", "by"}


def clean_domain(raw: str) -> str:
    """모델이 준 누리집 주소를 'bls.gov' 꼴로. 이상하면 ""."""
    d = (raw or "").strip().lower()
    d = re.sub(r"^https?://", "", d).split("/")[0]
    d = re.sub(r"^www\.", "", d)
    return d if DOMAIN.match(d) else ""


def host(url: str) -> str:
    return re.sub(r"^www\.", "", urllib.parse.urlparse(url).netloc.lower())


def on_domain(url: str, domain: str) -> bool:
    h = host(url)
    return bool(domain) and (h == domain or h.endswith("." + domain))


def english_keywords(info: dict) -> list[str]:
    """원어 검색어의 낱말(영문 원문에서 핵심어 확인용). 한국 기관이면 빈 목록."""
    words = re.findall(r"[A-Za-z][A-Za-z-]{2,}", info.get("원어검색어", ""))
    return list(dict.fromkeys(w for w in words if w.lower() not in EN_STOP))


SITE_MAX_RESULTS = 20   # 기관 누리집 안 검색은 라이너 최대치(20)까지 — 특정 데이터 페이지가 11위 밖에 있는 경우(11/14 FEC)


def _one(q: str, lang: str = "ko") -> list[dict]:
    try:
        rs = web_search(q, lang=lang, max_results=SITE_MAX_RESULTS if q.startswith("site:") else 10)
    except RuntimeError as e:  # 검색 하나 실패는 나머지로 계속(이유 출력)
        print(f"    라이너 검색 실패 '{q}': {e}")
        return []
    print(f"    라이너 검색 '{q}' (일반 경로{', 영어' if lang == 'en' else ''}) → {len(rs)}건")
    return rs


def query_lang(q: str) -> str:
    """원어 검색어의 언어: 일본어(가나·한자) 'ja', 중국어 한자만 'zh', 나머지 'en'(11/14 일본 임야청 백서)."""
    if re.search(r"[\u3040-\u30ff]", q):
        return "ja"
    if re.search(r"[\u4e00-\u9fff]", q):
        return "zh"
    return "en"


PRESS_SITES = {"gg.go.kr": "gnews.gg.go.kr"}   # 기관 누리집 → 보도자료 게시판이 있는 다른 주소


MAX_SINGLE_KEYWORDS = 2   # 핵심어 하나씩 쓰는 짧은 검색어 수


def diverse_queries(info: dict, domain: str) -> list[tuple[str, str]]:
    """종류가 다른 짧은 검색어(11/15, 연구 근거: AVeriTeC 2025 — 검색어 종류를 합치면 완전한 증거 6/20 → 19/20,
    QuanTemp++ — 짧고 서로 다른 검색 구로 Recall@100 0.51 → 0.82).
    - 출처를 묻는 검색어: '기관 핵심어 보도자료' / '핵심어 통계 발표'
    - 핵심어를 하나씩 쓴 짧은 검색어(기관 누리집 안, 기관 이름과)"""
    inst, keys = info.get("기관", ""), [k for k in info.get("핵심어", []) if k][:MAX_SINGLE_KEYWORDS]
    out = []
    if keys:
        out.append((f"{inst} {keys[0]} 보도자료".strip(), "ko"))
        out.append((f"{keys[0]} {info.get('기준시점', '')} 통계 발표 자료".strip(), "ko"))
    for k in keys:
        out.append((f"site:{domain} {k}" if domain else f"{inst} {k}".strip(), "ko"))
    return out


def search(info: dict) -> list[dict]:
    """일반 경로 검색어를 동시에 보낸다. 결과는 합쳐서 돌려준다(중복 주소 제거)."""
    domain = clean_domain(info.get("기관누리집", ""))
    terms = info.get("보고서") or " ".join(info["핵심어"][:3])
    period = info.get("기준시점", "")
    qs: list[tuple[str, str]] = []
    if domain:
        qs += [(f"site:{domain} {terms} {period}".strip(), "ko"),
               (f"site:{domain} filetype:pdf {terms} {period}".strip(), "ko")]
        # 보도자료가 본 누리집과 다른 주소에 있는 기관(11/14 경기도 gnews.gg.go.kr)
        press = PRESS_SITES.get(domain)
        if press:
            qs.append((f"site:{press} {' '.join(info['핵심어'][:2])}".strip(), "ko"))
    if info.get("원어검색어"):
        lang = query_lang(info["원어검색어"])
        qs.append((info["원어검색어"], lang))
        if domain:
            qs.append((f"site:{domain} {info['원어검색어']}", lang))
    if info.get("기관"):
        qs.append((f"{info['기관']} {terms} {period} filetype:pdf".strip(), "ko"))
    qs += diverse_queries(info, domain)
    qs = list(dict.fromkeys(qs))
    with ThreadPoolExecutor(max(1, len(qs))) as ex:
        answers = list(ex.map(lambda q: _one(*q), qs))
    out, seen = [], set()
    for rs in answers:
        for r in rs:
            if r.get("url") and r["url"] not in seen:
                seen.add(r["url"])
                out.append(r)
    return out


def finder(info: dict, results: list[dict]) -> list[Candidate]:
    """기관 누리집 안 문서(PDF·웹 페이지)를 앞에, 그다음 공공기관 PDF. 언론·블로그·SNS 는 뺀다."""
    domain = clean_domain(info.get("기관누리집", ""))
    own, public, seen = [], [], set()
    for r in results:
        url = r.get("url", "")
        if not url or url in seen or is_news_or_social(url, domain):
            continue
        seen.add(url)
        c = Candidate(r.get("title") or url, url, url, "gen_" + hashlib.sha1(url.encode()).hexdigest()[:12])
        if r.get("description"):
            _snippets.setdefault(url, str(r["description"]))
        if on_domain(url, domain):
            own.append(c)
        elif PUBLIC_PDF.match(url):
            public.append(c)
    # 자바스크립트로만 보이는 페이지(AP·오늘의집 등)는 받아도 본문이 비어 있다 → 기관 누리집 결과의 요약 글에
    # 기사 숫자가 있으면 그 요약 글을 따로 후보로(화면에 '검색 요약으로 확인' 표시)
    snips = []
    for c in own:
        desc = _snippets.get(c.pdf_url, "")
        if desc and any(to_regex(v.text).search(normalize_numbers(desc)) for v in variants(info.get("숫자", ""))):
            snips.append(Candidate(c.title, c.page_url, c.pdf_url, "gen_snip_" + c.file_key[4:]))
    return (snips + own + public)[:MAX_CANDIDATES]


def html_text(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")
    text = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h\d)>", "\n", text)
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"[ \t\r\f\v\u00a0\u202f]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()
    return normalize_numbers(text)


def normalize_numbers(text: str) -> str:
    """외국 원문 숫자 표기를 기사 표기에 맞춘다: 소수점 쉼표 '3,4 %' → '3.4%', '0.3 percent' → '0.3%'.
    (11/14: Insee 원문 페이지를 찾고도 '3,4 %'라 3.4%를 못 맞춤) 천 단위 쉼표(1,234)는 건드리지 않는다."""
    text = re.sub(r"(\d),(\d{1,2}) ?%", r"\1.\2%", text)
    return re.sub(r"(\d) ?(?:percent|per cent|pour cent)\b", r"\1%", text, flags=re.I)


def clean_url(url: str) -> str:
    """검색 결과 주소의 세션 꼬리표(';jsessionid=…', 인코딩된 '%3Bjsessionid=…')를 뗀다(그대로면 받기 실패)."""
    return re.sub(r"(?:%3B|;)jsessionid=[^?&#]*", "", url, flags=re.I)


def fetch_doc(c: Candidate) -> Path | None:
    """일반 경로 후보를 받는다. 이미 받은 것은 다시 받지 않는다. 실패·빈 페이지면 None.
    - PDF → 원본PDF/<키>.pdf
    - HWP·HWPX → 웹문서/<키>.txt (글자만)
    - 웹 페이지 → 웹문서/<키>.txt: 본문 글자 + 첨부파일(PDF·HWP·HWPX, 최대 3개)의 글자.
      국내 기관 원문은 대개 게시물 첨부파일에 있다(11/14)."""
    pdf, txt = PDF_DIR / f"{c.file_key}.pdf", WEB_DIR / f"{c.file_key}.txt"
    for p in (pdf, txt):
        if p.exists():
            return p
    if c.file_key.startswith("gen_snip_"):   # 검색 요약 글 후보(finder 가 만든 것)
        WEB_DIR.mkdir(parents=True, exist_ok=True)
        txt.write_text(f"{SNIPPET_MARK} {normalize_numbers(_snippets.get(c.pdf_url, ''))}", encoding="utf-8")
        return txt
    url = clean_url(c.pdf_url)
    try:
        data = fetch_bytes(url)
    except RuntimeError as e:
        # 자동 접속을 막는 누리집(bls.gov 403): 라이너 검색 결과에 붙은 그 페이지 요약 글로라도 대조한다
        if "HTTP 403" in str(e) and _snippets.get(c.pdf_url):
            print(f"    자동 접속이 막혀 라이너 검색 요약 글로 대조: {url}")
            WEB_DIR.mkdir(parents=True, exist_ok=True)
            txt.write_text(f"{SNIPPET_MARK} {normalize_numbers(_snippets[c.pdf_url])}", encoding="utf-8")
            return txt
        raise
    kind = 문서읽기.kind_of(data)
    if kind == "pdf":
        PDF_DIR.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(data)
        return pdf
    if kind in ("hwp", "hwpx", "xlsx"):
        text = normalize_numbers(문서읽기.file_text(data))
    elif kind == "html":
        text = html_text(data)
        page = data.decode("utf-8", "replace")
        for name, link in 문서읽기.attachments(page, url):
            try:
                body = 문서읽기.file_text(fetch_bytes(link))
            except RuntimeError as e:  # 첨부 하나 실패는 건너뜀(이유 출력)
                print(f"    첨부파일 받기·읽기 실패({name}): {e}")
                continue
            if body.strip():
                print(f"    첨부파일 글자 더함: {name} ({len(body)}자)")
                text += f"\n[첨부: {name}]\n" + normalize_numbers(body)
    else:
        print(f"    PDF·HWP·웹 페이지가 아니라 건너뜀: {url}")
        return None
    if len(text) < MIN_WEB_CHARS:
        print(f"    글자가 너무 적어 건너뜀({len(text)}자): {url}")
        return None
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    txt.write_text(text, encoding="utf-8")
    return txt


def web_pages(path: Path) -> list[str]:
    """웹 페이지 글자를 WEB_PAGE_CHARS 씩 나눈 '부분' 목록(문단 경계에서 자름)."""
    text = path.read_text(encoding="utf-8")
    parts, cur = [], ""
    for para in text.split("\n"):
        if len(cur) + len(para) > WEB_PAGE_CHARS and cur:
            parts.append(cur)
            cur = ""
        cur += para + "\n"
    if cur.strip():
        parts.append(cur)
    return parts


def register_snippet(url: str, description: str) -> None:
    """다른 경로(라이너 Scholar 등)의 검색 요약 글을 등록 — 본문을 못 받을 때 그 요약으로 대조."""
    _snippets[url] = description


def snippet_of(url: str) -> str:
    """검색 결과 요약 글(없으면 ''). 받기 전 후보 순위를 매길 때 제목과 같이 쓴다."""
    return _snippets.get(url, "")


def is_snippet(path: Path | str) -> bool:
    p = Path(path)
    return p.suffix == ".txt" and p.exists() and p.read_text(encoding="utf-8")[:len(SNIPPET_MARK)] == SNIPPET_MARK


def is_web(path: Path | str) -> bool:
    return str(path).endswith(".txt")
