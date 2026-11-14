"""세 기관의 발간 목록을 미리 모아 두고, 기사마다 그 목록에서 원 보고서 후보를 먼저 찾는다.

왜: 지금까지는 기사마다 라이너 검색 결과에만 기대 후보를 모아, 검색 결과가 바뀌면 고른 보고서도 바뀌었다
    (같은 문장도 실행마다 결과가 흔들림, 원 자료를 못 찾음 — 11/13 평가). 목록에서 찾으면 결과가 일정하고 빠르다.
출처(모두 기관 누리집 공개 목록):
- 자본시장연구원: 보고서 목록 JSON(전체) + 『자본시장포커스』 목록
- 한국보건사회연구원: 보고서 전체·이슈앤포커스·보건복지포럼(글 단위)·Research in Brief 목록
- 국가데이터처: '보도자료 전체' 게시판(PDF 가 붙은 글)
저장: 작업/목록/<기관>.json. REFRESH_HOURS 가 지나면 최신 쪽부터 다시 읽고, 이미 아는 글만 나오는 쪽이
      KNOWN_STOP 번 이어지면 멈춘다(처음엔 쪽 상한까지). 한 쪽 읽을 때마다 저장(중간에 끊겨도 이어서).
찾기: 기사가 보고서 이름을 밝혔으면 제목 일치(핵심 낱말이 모두 있어야), 아니면 핵심어로 추린 뒤 라이너 Rerank.
"""
import html
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

from PDF도구 import title_similarity
from 기관별 import MODS_BOARD, MODS_ITEM, MODS_REF_BID, Candidate, fetch_bytes, fetch_text
from 순위 import rerank_scores

LIST_DIR = Path(__file__).resolve().parent.parent / "작업" / "목록"
REFRESH_HOURS = 24
KNOWN_STOP = 2               # 이미 아는 글만 나온 쪽이 이만큼 이어지면 그만 읽음
NAMED_MIN = 0.6              # 기사가 밝힌 이름과 이만큼 비슷하고 핵심 낱말이 다 있으면 후보
NAMED_PICKS = 3
KEYWORD_POOL = 40            # 이름이 없을 때 핵심어로 먼저 추릴 개수(그다음 Rerank)
RERANK_MIN = 0.2
RERANK_PICKS = 2

KCMI = "https://www.kcmi.re.kr"
KCMI_FOCUS_ITEM = re.compile(r"viewpage\((\d+),'\./pub_dt_view','([^']+)'\);\">\s*<strong>(.*?)</strong>\s*"
                             r"<b>(.*?)</b>\s*<span>(.*?)</span>", re.S)
KCMI_PDF = re.compile(r"/common/downloadw\?fid=(\d+)&fgu=\d+&fty=\d+")
KIHASA = "https://www.kihasa.re.kr"
KIHASA_LISTS = {"보고서": ("/publish/report/all/list", 80), "이슈앤포커스": ("/publish/regular/focus/list", 60),
                "브리프": ("/publish/regular/brief/list", 20)}
# 보건복지포럼 목록은 page= 를 무시하고 최신 호만 준다 → 호수 선택 상자의 값(volume=)으로 호마다 읽는다
HSW_LIST = "/publish/regular/hsw/list"
HSW_VOLUME = re.compile(r'<option[^>]*value="(\d+)"[^>]*>\s*(20\d{2}년 \d{2}월 통권 제\d+호)')
HSW_VOLUMES = 60             # 약 5년
KIHASA_TITLE = re.compile(r'<span class="title"><a href="([^"]+)">\s*(.*?)\s*</a></span>(.*?)(?=<span class="title">|</ul>)', re.S)
KIHASA_FILE = re.compile(r"/api/kihasa/file/download\?seq=(\d+)")
MODS_PAGES = 150             # 쪽당 약 12건 → 약 5년


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


# ── 기관별 목록 한 쪽 읽기: [{제목, 날짜, 주소, pdf, 열쇠, 종류}] ──────────────────
def _kcmi_reports() -> list[dict]:
    data = json.loads(fetch_bytes(KCMI + "/report/json_report_list", b"thispage=1&perpage=3000"))
    return [{"제목": r.get("report_title", ""), "날짜": r.get("pub_date", ""), "요약": (r.get("summary") or "")[:300],
             "주소": f"{KCMI}/report/report_view?report_no={r.get('report_no')}",
             "pdf": KCMI + r["report_pdf_download_link"] if r.get("report_pdf_download_link") else "",
             "열쇠": f"kcmi_{r.get('report_no')}", "종류": r.get("report_type_name", "보고서")} for r in data]


def _kcmi_focus(page: int) -> list[dict]:
    raw = fetch_bytes(KCMI + "/publications/getpubdt.php", f"pg={page}&zcd=002001016&fcd=002001016001".encode())
    return [{"제목": f"{_clean(title)} (자본시장포커스 {_clean(byline)})", "날짜": "", "요약": _clean(summary)[:300],
             "주소": f"{KCMI}/publications/pub_dt_view?cno={idx}&{params}&pg={page}", "pdf": "",
             "열쇠": f"kcmi_focus_{idx}", "종류": "자본시장포커스"}
            for idx, params, title, byline, summary in KCMI_FOCUS_ITEM.findall(raw.decode("utf-8", "replace"))]


def _kihasa_page(kind: str, path: str, page: int) -> list[dict]:
    raw = fetch_text(f"{KIHASA}{path}{'' if path.endswith('&') else '?'}page={page}")
    out = []
    for href, title, rest in KIHASA_TITLE.findall(raw):
        files = KIHASA_FILE.findall(rest)   # 목록에 다운로드 링크가 없는 보고서는 상세 페이지에서(후보로 뽑힐 때)
        view = KIHASA + html.unescape(href).split("&page=")[0].replace("?page=1&", "?")
        details = [_clean(p) for p in re.findall(r"<p>(.*?)</p>", rest.split('class="category"')[0], re.S)]
        out.append({"제목": _clean(title), "날짜": next((d for d in details if re.search(r"20\d{2}", d)), ""),
                    "요약": " · ".join(details)[:200], "주소": view,
                    "pdf": f"{KIHASA}/api/kihasa/file/download?seq={files[0]}" if files else "",
                    "열쇠": f"kihasa_{files[0]}" if files else "kihasa_view_" + re.sub(r"\D", "", view.split("seq=")[-1]),
                    "종류": kind})
    return out


def _hsw_volumes() -> list[tuple[str, str]]:
    return HSW_VOLUME.findall(fetch_text(KIHASA + HSW_LIST))[:HSW_VOLUMES]


def _hsw_page(volumes: list[tuple[str, str]], i: int) -> list[dict]:
    """i번째(1부터) 호의 글들. 제목 뒤에 호 정보를 붙인다."""
    if i > len(volumes):
        return []
    seq, label = volumes[i - 1]
    items = _kihasa_page("보건복지포럼", f"{HSW_LIST}?volume={seq}&", 1)
    return [{**it, "제목": f"{it['제목']} (보건복지포럼 {label})", "날짜": label[:9]} for it in items]


def _mods_page(page: int) -> list[dict]:
    query = urllib.parse.urlencode({"mid": "a10301010000", "bid": "a103010100", "ref_bid": MODS_REF_BID, "nPage": page})
    out = []
    for path, bid, list_no, title in MODS_ITEM.findall(fetch_text(f"{MODS_BOARD}?{query}")):
        out.append({"제목": title.strip(), "날짜": "", "요약": "",
                    "주소": f"{MODS_BOARD}?mid=a10301010000&bid={bid}&act=view&list_no={list_no}",
                    "pdf": "https://mods.go.kr" + html.unescape(path), "열쇠": f"mods_{list_no}", "종류": "보도자료"})
    return out


# ── 모으기(중간 저장·이어 읽기) ───────────────────────────────────────────────
def _load(inst: str) -> dict[str, dict]:
    path = LIST_DIR / f"{inst}.json"
    if not path.exists():
        return {}
    try:
        return {it["열쇠"]: it for it in json.loads(path.read_text(encoding="utf-8"))}
    except (json.JSONDecodeError, OSError, KeyError) as e:
        print(f"    발간 목록 파일이 깨져 새로 만듦: {path.name} — {e}")
        return {}


def _save(inst: str, items: dict[str, dict]) -> None:
    LIST_DIR.mkdir(parents=True, exist_ok=True)
    (LIST_DIR / f"{inst}.json").write_text(json.dumps(list(items.values()), ensure_ascii=False), encoding="utf-8")


def _paged(inst: str, items: dict, reader, max_pages: int) -> None:
    """1쪽부터 읽다가, 새 글이 없는 쪽이 KNOWN_STOP 번 이어지면 멈춘다(처음엔 max_pages 까지)."""
    quiet = 0
    for page in range(1, max_pages + 1):
        try:
            got = reader(page)
        except RuntimeError as e:  # 한 쪽 실패는 이유를 남기고 다음 쪽으로
            print(f"    {inst} 목록 {page}쪽 읽기 실패: {e}")
            continue
        if not got:
            break
        new = [it for it in got if it["열쇠"] not in items]
        for it in got:
            items[it["열쇠"]] = {**items.get(it["열쇠"], {}), **it}
        _save(inst, items)
        quiet = 0 if new else quiet + 1
        if quiet >= KNOWN_STOP:
            break


def refresh(inst: str, force: bool = False) -> dict[str, dict]:
    """목록 파일이 REFRESH_HOURS 보다 오래됐으면(또는 force) 최신 쪽부터 다시 읽는다."""
    items = _load(inst)
    path = LIST_DIR / f"{inst}.json"
    if items and not force and time.time() - path.stat().st_mtime < REFRESH_HOURS * 3600:
        return items
    print(f"    {inst} 발간 목록 갱신 중…")
    if inst == "자본시장연구원":
        try:
            for it in _kcmi_reports():
                items[it["열쇠"]] = it
            _save(inst, items)
        except (RuntimeError, ValueError) as e:
            print(f"    자본시장연구원 보고서 목록 읽기 실패: {e}")
        _paged(inst, items, _kcmi_focus, 60)
    elif inst == "한국보건사회연구원":
        for kind, (path_, pages) in KIHASA_LISTS.items():
            _paged(inst, items, lambda p, k=kind, pa=path_: _kihasa_page(k, pa, p), pages)
        try:
            volumes = _hsw_volumes()
            _paged(inst, items, lambda i: _hsw_page(volumes, i), len(volumes))
        except RuntimeError as e:
            print(f"    보건복지포럼 호수 목록 읽기 실패: {e}")
    elif inst == "국가데이터처":
        _paged(inst, items, _mods_page, MODS_PAGES)
    _save(inst, items)
    return items


# ── 찾기 ─────────────────────────────────────────────────────────────────────
def _to_candidate(it: dict) -> Candidate | None:
    pdf, key = it["pdf"], it["열쇠"]
    if not pdf:  # 목록에 PDF 주소가 없던 글: 상세 페이지에서 찾는다(자본시장포커스·보사연 일부 보고서)
        pattern, base = (KCMI_PDF, KCMI) if key.startswith("kcmi_") else (KIHASA_FILE, KIHASA)
        try:
            link = pattern.search(fetch_text(it["주소"]))
        except RuntimeError as e:
            print(f"    {e}")
            return None
        if not link:
            return None
        pdf = base + link.group(0)
        if key.startswith("kihasa_view_"):
            key = f"kihasa_{link.group(1)}"
    return Candidate(it["제목"], it["주소"], pdf, key)


def find(inst: str | None, info: dict, query: str) -> list[Candidate]:
    """발간 목록에서 기사와 맞는 글 → 후보(앞일수록 유력). 목록이 없는 기관이면 []."""
    from 후보문서 import missing_nouns  # 순환 import 피하기(후보문서 → PDF도구 …)
    if inst not in ("자본시장연구원", "한국보건사회연구원", "국가데이터처"):
        return []
    items = list(refresh(inst).values())
    name = info.get("보고서") or ""
    picks: list[dict] = []
    if name:
        scored = [(title_similarity(name, it["제목"]), it) for it in items if missing_nouns(name, it["제목"]) == 0]
        period = re.findall(r"20\d{2}년|\d{1,2}월|\d분기", info.get("기준시점") or "")
        scored = [(s + 0.1 * sum(p.replace(" ", "") in it["제목"].replace(" ", "") for p in period), it)
                  for s, it in scored if s >= NAMED_MIN]
        picks = [it for _, it in sorted(scored, key=lambda x: -x[0])[:NAMED_PICKS]]
    if not picks:
        words = [w for k in info.get("핵심어", []) for w in re.findall(r"[가-힣A-Za-z]{2,}", k)]
        pool = sorted(items, key=lambda it: -sum(w in it["제목"] + it.get("요약", "") for w in words))[:KEYWORD_POOL]
        pool = [it for it in pool if any(w in it["제목"] + it.get("요약", "") for w in words)]
        scores = rerank_scores(query, [f"{it['제목']} {it.get('요약', '')}" for it in pool]) if pool else []
        picks = [it for s, it in sorted(zip(scores, pool), key=lambda x: -x[0]) if s >= RERANK_MIN][:RERANK_PICKS]
    out = [c for c in (_to_candidate(it) for it in picks) if c]
    for c in out:
        print(f"    발간 목록 후보: {c.title[:45]}")
    return out


if __name__ == "__main__":  # 목록 모으기만: python3 도구/발간목록.py [기관]
    for inst in sys.argv[1:] or ["자본시장연구원", "한국보건사회연구원", "국가데이터처"]:
        n = len(refresh(inst, force=True))
        print(f"{inst}: {n}건 → {LIST_DIR / (inst + '.json')}")
