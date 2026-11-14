"""「원래 어디서?」 숫자가 든 기사 문장 → 원 보고서 PDF의 쪽·문장 + 판정.

사용: python3 도구/원문찾기.py "기사 문장" ["기사 문맥(제목·앞뒤 문단)"] ["기사 날짜"]
판정: 일치 / 표현 바뀜 / 숫자 다름 / 기사가 계산 / 통계표·원자료 인용 / 원문에 없음 / 원출처 미확인 / 확인 대상 아님
기본은 일반 경로(일반경로.py): 어떤 기관이든 공식 누리집(PDF·웹 페이지)에서 찾는다.
세 기관(자본시장연구원·국가데이터처·한국보건사회연구원)은 전용 코드(기관별.py·발간목록.py)가 보조로 더 확실히 받는다.

흐름: 라이너 모델(문장 분해) → 라이너 검색(site:기관 누리집 + 일반) → PDF·웹 페이지 후보
      → 기준 시점이 맞는 PDF 우선 → 숫자 후보 순위 → 라이너 모델이 같은 지표 고르기·판정
      → 못 찾으면 라이너 게이트웨이 모델이 PDF 직접 읽기 → 그래도 없으면 계산·원자료 여부
"""
import hashlib
import json
import os
import re
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime
from pathlib import Path

import 계산찾기
import 캐시
import 기록
import 다른값찾기
import 발간목록
import 미확인안내
import 입구
import 공시찾기
import 논문찾기
import 일반경로
import 정책브리핑
import 라이너에이전트
import 여론조사찾기
import 시청률찾기
import 출처종류
import 판정규칙
from liner_api import web_search
from PDF도구 import squash, find_candidates, period_tokens
from 검색 import SKIP_LEAD, is_recent, search_all
from 기관별 import FINDERS, Candidate, drop_rebuttals, normalize_institution
from 기사정리 import all_numbers, extract_cached, is_condition_number, stabilize
from 숫자표기 import to_regex, variants
from 순위 import cross_check, rank_hits, same_indicator
from 판정 import choose, mentions_raw_data, near_match, read_pdf
from 후보문서 import MAX_DOWNLOAD, Doc, load_docs, missing_nouns, named, region_penalty

# 시연용: 이 환경변수가 1이면 같은 문장(문맥·날짜 포함)의 결과를 저장해 두었다가 바로 다시 보여 준다.
# 평가 때는 켜지 말 것(실행마다의 흔들림을 숨기게 됨). 예: LINER_RESULT_CACHE=1 python3 도구/웹서버.py
RESULT_CACHE_ENV = "LINER_RESULT_CACHE"
RESULT_DIR = Path(__file__).resolve().parent.parent / "작업" / "결과"
KOSIS_SEARCH = "https://kosis.kr/search/search.do?query="
MAX_TABLES = 3
MAX_PRIORITY_EACH = 2   # 전용 출처마다 꼭 받을 후보 수
MAX_PRIORITY = 4        # 꼭 받을 후보 전체 상한(나머지 자리는 제목·요약 순위로)
MAX_DOCS_TO_JUDGE = 4     # 11/14 저녁: 3 → 4. 숫자 후보가 나온 문서 중 모델 판정까지 할 개수(11/14: 2 → 3, 같은 이름 다른 달 보고서가 앞을 채움)


def raw_data_result(result: dict, info: dict) -> dict:
    """기사가 보고서가 아니라 통계표(KOSIS)·원자료를 인용한 경우.
    라이너 site:kosis.kr 검색에서 통계표 화면(statHtml)만 후보로 고르고, KOSIS 통합검색 주소도 함께 준다."""
    words = " ".join(info["핵심어"])
    try:
        tables = web_search(f"site:kosis.kr {info.get('보고서', '')} {words} 통계표")
    except RuntimeError as e:
        print(f"    KOSIS 통계표 검색 실패: {e}")
        tables = []
    picks = [{"제목": r.get("title"), "주소": r.get("url")} for r in tables
             if "kosis.kr" in r.get("url", "") and "statHtml" in r.get("url", "")][:MAX_TABLES]
    picks.append({"제목": f"KOSIS에서 직접 찾기: {words}",
                  "주소": KOSIS_SEARCH + urllib.parse.quote(words)})
    result.update(판정="통계표·원자료 인용",
                  이유="기사가 보고서가 아니라 통계표(KOSIS)·원자료를 인용. 보도자료 PDF에서는 확인 못 함",
                  통계표후보=picks)
    return result


def apply_rules(result: dict, sentence: str, number: str) -> None:
    """코드 규칙(%↔%p, 증감 방향, 배↔%, 비교 기간)으로 찾은 차이를 판정에 반영한다.
    모델이 '일치'라 해도 규칙이 다름을 찾으면 '표현 바뀜'으로 내린다."""
    diffs = 판정규칙.check(sentence, number, result.get("원문인용", ""), result.get("찾은표기", ""))
    result["규칙확인"] = diffs
    if not diffs:
        return
    have = {d.get("항목") for d in result.get("차이", [])}
    for d in diffs:
        if d["항목"] not in have:
            result["차이"].append({"항목": d["항목"], "기사": d["기사"], "원문": d["원문"], "근거": "규칙: " + d["규칙"]})
    if result["판정"] == "일치":
        result["판정"] = "표현 바뀜"
        print(f"    규칙 확인: {', '.join(d['규칙'] for d in diffs)} → '일치'를 '표현 바뀜'으로")


def found_result(result: dict, doc: Doc, pick: dict, how: str, sentence: str, number: str) -> dict:
    hit = pick["후보"]
    result.update(판정=pick["판정"], 보고서=doc.cand.title, 상세페이지=doc.cand.page_url, PDF=str(doc.pdf),
                  쪽=hit["쪽"], 찾은표기=hit["표기"], 표기종류=hit["종류"], 찾은방법=how,
                  원문인용=pick["원문인용"], 차이=list(pick["차이"]), 확인못한부분=pick["확인못한부분"],
                  칸별비교=pick.get("칸", {}))
    if 일반경로.is_snippet(doc.pdf):  # 원문 페이지를 직접 열지 못하고 검색 요약 글로 확인한 경우
        result["찾은방법"] = how + " — 원문 페이지는 자동 접속이 막혀, 라이너 검색 결과의 그 페이지 요약 글로 확인"
        result["확인못한부분"] = ("원문 페이지를 직접 열지 못해 검색 요약 글로 확인했습니다. 링크를 열어 직접 확인해 주세요. "
                            + (result.get("확인못한부분") or "")).strip()
    if "표 줄" in hit["종류"]:  # 표 줄 하나만으로는 무슨 지표인지 모름 → 표 제목·단위·열 머리를 같이 남긴다
        result["표문맥"] = hit["원문"]
    evidence = result.get("표문맥") or pick["원문인용"]
    apply_rules(result, sentence, number)
    기록.emit("판정", dict(result))   # 화면에 먼저 보여 주고, 교차 확인은 뒤에서 채운다
    with 기록.step("교차 확인(Decisions)"):
        result["교차확인"] = cross_check(sentence, evidence, result["판정"])
    return result


def run(sentence: str, context: str = "", article_date: str = "", force: bool = False,
        on_event=None, prefer: dict | None = None) -> dict:
    """sentence: 숫자가 든 문장. context: 기사 제목·앞뒤 문단(선택). article_date: 기사 날짜(선택)
    force: 입구 필터가 '통계 문장 아님'이라 해도 확인. on_event(종류, 데이터): 화면 진행 표시용
    prefer: 사람이 '다른 후보'에서 고른 보고서 {"제목","상세페이지","PDF주소","키"} — 검색 없이 이것만 대조"""
    cache_key = json.dumps([sentence, context, article_date, force, prefer], ensure_ascii=False)
    if os.environ.get(RESULT_CACHE_ENV) == "1" and (hit := 캐시.get("결과", cache_key)) is not None:
        print("    (시연용: 같은 문장의 저장된 결과를 다시 보여 줌)")
        return {**hit, "다시보기": True}
    기록.start(on_event)
    result = {"입력": sentence, "시각": datetime.now().isoformat(timespec="seconds")}
    try:
        result = _run_with_limit(result, sentence, context, article_date, force, prefer)
        # 못 찾았을 때 '왜 없는지·어디서 확인할지'(A1~A3)
        result = _guard_news(_guard_condition(result))
        if result.get("판정") == "원출처 미확인":
            result["확인할위치"] = _spots(result, sentence)
        result = {k: v for k, v in result.items() if not k.startswith("_")}   # 진행용 임시 값은 결과에 남기지 않음
        with 기록.step("확인할 곳 안내"):
            result = 미확인안내.attach(미확인안내.to_raw_data(result), sentence)
        return result
    except 기록.TimeUp as e:   # 시간 상한 뒤 늦게 끝난 작업(결과는 이미 답함)
        print(f"    {e}")
        return result
    finally:
        result["시간"] = 기록.summary()
        print(f"    걸린 시간 {result['시간']['총초']}초 — 단계별 {result['시간']['단계초']}")
        if os.environ.get(RESULT_CACHE_ENV) == "1" and result.get("판정"):
            캐시.put("결과", cache_key, result)


TIME_LIMIT_SEC = int(os.environ.get("ORIGIN_TIME_LIMIT", "150"))   # 한 건 전체 시간 상한(결선 생중계에서 멈추지 않게)


def _run_with_limit(result: dict, sentence: str, context: str, article_date: str, force: bool,
                    prefer: dict | None) -> dict:
    """_run 을 TIME_LIMIT_SEC 까지만 기다린다. 넘으면 그때까지 모은 후보·검색 링크로 '원출처 미확인(시간 초과)'.
    (11/15: 캐시 없는 실행에서 PDF 직접 읽기 대기로 한 건이 467초까지 걸린 적이 있음)"""
    ex = ThreadPoolExecutor(1)
    future = ex.submit(_run, result, sentence, context, article_date, force, prefer)
    try:
        return future.result(timeout=TIME_LIMIT_SEC)
    except FutureTimeout:
        기록.stop()   # 남은 작업은 다음 단계에 들어서면 멈춘다
        print(f"    시간 상한 {TIME_LIMIT_SEC}초를 넘겨 지금까지 찾은 것으로 답함")
        links = result.get("_진행후보") or result.get("_검색링크") or []
        partial = {k: v for k, v in result.items() if not k.startswith("_")}
        partial.update(판정="원출처 미확인", 시간초과=True, 후보=links[:LEAD_LINKS],
                       이유=(f"{TIME_LIMIT_SEC}초 안에 원문 대조를 마치지 못했습니다(누리집 응답이 느리거나 문서가 큼). "
                           "지금까지 찾은 후보 문서를 보여 드립니다. 잠시 뒤 다시 확인하면 저장된 자료로 더 빨리 끝납니다."))
        return partial
    finally:
        ex.shutdown(wait=False)


SECOND_ROUND_NEEDS_SEC = 45    # 2라운드에 필요한 시간(남은 시간이 이보다 적으면 안 함 — 11/15: 25초 고정이면 Deep Research 뒤 늘 건너뜀)
SECOND_ROUND_SNIPPETS = 6      # 2라운드에 쓸 다른 기사 제목·요약 수


def _run(result: dict, sentence: str, context: str, article_date: str, force: bool,
         prefer: dict | None = None) -> dict:
    """1차로 못 찾으면, 같은 숫자를 다룬 다른 기사들의 제목·요약을 문맥으로 넣어 기관·보고서를 다시 뽑고 한 번 더 찾는다.
    (11/15, 연구 근거: 1차에서 못 찾으면 재인용 기사에서 출처 이름을 얻어 반복 검색 — QuanTemp++ 저자 제안, 최대 2라운드)"""
    # 속도: Deep Research 를 처음부터 동시에 출발시킨다(우리 방식이 못 찾을 때 기다리는 시간을 줄임, 11/15).
    # 우리 방식이 찾으면 그 답은 안 쓰지만 저장되어 다음에 다시 쓴다.
    prefetch = None
    if DEEP_RESEARCH_ON and DEEP_RESEARCH_PREFETCH and not prefer:
        prefetch = ThreadPoolExecutor(1).submit(라이너에이전트.ask, sentence, context, TIME_LIMIT_SEC - 20)
    first = _run_once(result, sentence, context, article_date, force, prefer)
    if prefer or first.get("판정") != "원출처 미확인" or first.get("출처종류") == "의원실 자료":
        return first
    deep = _deep_research_round(result, sentence, context, article_date, force, first, prefetch)
    if deep:
        return deep
    if 기록.summary()["총초"] > TIME_LIMIT_SEC - SECOND_ROUND_NEEDS_SEC:
        return first   # 시간 상한 안에 2라운드를 마칠 시간이 없음
    snippets = [f"{r.get('title', '')}: {str(r.get('description', ''))[:200]}"
                for r in (first.get("_검색결과") or [])[:SECOND_ROUND_SNIPPETS]]
    if not snippets:
        return first
    context2 = (context + " [같은 숫자를 다룬 다른 기사] " + " / ".join(snippets)).strip()
    info1 = first.get("추출") or {}
    print("[2라운드] 다른 기사들의 제목·요약으로 기관·보고서를 다시 뽑아 한 번 더 찾음")
    second = _run_once({k: v for k, v in result.items() if k in ("입력", "시각")}, sentence, context2,
                       article_date, force, None)
    info2 = second.get("추출") or {}
    same = all(info1.get(k) == info2.get(k) for k in ("기관", "보고서", "기관누리집", "원어검색어"))
    if second.get("판정") in FOUND | {NEAR, "통계표·원자료 인용"}:
        second["찾은방법"] = (second.get("찾은방법") or "") + " — 2라운드(같은 숫자를 다룬 다른 기사에서 출처 이름을 얻어 다시 찾음)"
        second["2라운드"] = {"1차추출": info1, "2차추출": info2}
        return second
    if same:
        print("    2라운드도 같은 기관·보고서라 1차 결과를 씀")
    return first


DEEP_RESEARCH_ON = os.environ.get("ORIGIN_DEEP_RESEARCH", "1") == "1"   # 라이너 Deep Research 후보 쓰기(끄기: 0)
DEEP_RESEARCH_PREFETCH = os.environ.get("ORIGIN_DEEP_RESEARCH_PREFETCH", "1") == "1"   # 처음부터 동시에 묻기(끄기: 0 — 비용 절약)


def _deep_research_round(result: dict, sentence: str, context: str, article_date: str, force: bool,
                         first: dict, prefetch=None) -> dict | None:
    """우리 방식으로 못 찾으면 라이너 Deep Research 가 원 출처로 지목한 주소를 받아 우리 코드로 확인한다.
    확정 판정(일치 등)이 나올 때만 쓰고, 아니면 None(1차 결과 유지). Deep Research 의 인용을 그대로 믿지 않는다."""
    if not DEEP_RESEARCH_ON or not (first.get("입구") or {}).get("통과", True):
        return None
    left = TIME_LIMIT_SEC - 기록.summary()["총초"] - 15   # 확인할 시간 남기기
    if left < 20:
        return None
    print("[딥리서치] 우리 방식으로 못 찾아 라이너 Deep Research 에 원 출처를 물음")
    with 기록.step("라이너 Deep Research"):
        if prefetch is not None:   # 처음부터 동시에 물어 둔 답
            try:
                ans = prefetch.result(timeout=left)
            except Exception as e:  # 시간 안에 안 오면 후보 없이(이유 출력)
                print(f"    Deep Research 답을 시간 안에 받지 못함: {type(e).__name__}")
                ans = {"오류": str(e)}
        else:
            ans = 라이너에이전트.ask(sentence, context, timeout=left)
    sources = 라이너에이전트.named_sources(ans) if "오류" not in ans else []
    first["_딥리서치참고"] = list(sources) + list((ans or {}).get("참고") or [])   # 못 찾아도 원문 위치 안내에 씀
    if not sources:
        print("    Deep Research 가 지목한 원 출처 주소가 없음")
        return None
    domain = 일반경로.clean_domain((first.get("추출") or {}).get("기관누리집", ""))
    info1 = first.get("추출") or {}
    names = f"{info1.get('기관', '')} {info1.get('원어검색어', '')} {info1.get('보고서', '')}"
    ok = lambda u: 일반경로.trusted_source(u, domain, names, sentence)   # noqa: E731
    dropped = [x["주소"] for x in sources if not ok(x["주소"])]
    sources = [x for x in sources if ok(x["주소"])]
    if dropped:
        print(f"    기관 누리집·공공기관이 아닌 주소(기사 등)는 후보에서 뺌: {[u[:50] for u in dropped]}")
    cands = []
    for x in sources:
        c = 공시찾기.candidate_from_url(x["주소"], x["제목"]) or Candidate(
            x["제목"] or x["주소"], x["주소"], x["주소"], "gen_" + hashlib.sha1(x["주소"].encode()).hexdigest()[:12])
        cands.append(c)
    number = (first.get("추출") or {}).get("숫자", "")
    cands = 라이너에이전트.snippet_candidates(ans, sources, number) + cands
    print(f"    Deep Research 가 지목한 원 출처 {len(cands)}곳을 받아 직접 확인: {[c.page_url[:60] for c in cands]}")
    again = _run_once({k: v for k, v in result.items() if k in ("입력", "시각")}, sentence, context,
                      article_date, force, None, extra=cands)
    if again.get("판정") in FOUND | {NEAR}:
        again["찾은방법"] = (again.get("찾은방법") or "") + " — 라이너 Deep Research 가 지목한 원 출처를 받아 우리가 직접 대조"
        again["딥리서치후보"] = [c.page_url for c in cands]
        return again
    print("    Deep Research 후보에서도 확인하지 못함 → 1차 결과 유지")
    return None


MAX_SPOTS = 3


def _spots(result: dict, sentence: str) -> list[dict]:
    """숫자는 확인 못 했지만 원문이 있을 곳: 우리 검색·Deep Research 참고 중 기관 누리집·공공기관·학술·IR·
    기사가 밝힌 외신 주소(언론 기사·검색 포털은 뺌). 사람이 열어 확인할 수 있게 원문 위치로 알려 준다."""
    info = result.get("추출") or {}
    domain = 일반경로.clean_domain(info.get("기관누리집", ""))
    names = f"{info.get('기관', '')} {info.get('원어검색어', '')} {info.get('보고서', '')}"
    rows = [{"제목": r.get("title") or r.get("제목") or "", "주소": r.get("url") or r.get("주소") or "",
             "요약": str(r.get("description") or "")}
            for r in (result.get("_기관검색결과") or []) + (result.get("_딥리서치참고") or []) + (result.get("_검색결과") or [])]
    cited = 일반경로.cited_outlet_domains(sentence)
    pats = [to_regex(v.text) for n in all_numbers(sentence) for v in variants(n)]
    words = {w for k in info.get("핵심어", []) for w in re.findall(r"[가-힣A-Za-z]{2,}", k)} | set(
        w.lower() for w in re.findall(r"[A-Za-z]{4,}", info.get("원어검색어", "")))

    def relevant(r: dict) -> bool:
        """그 페이지가 이 숫자와 관계있는지: 요약에 문장 숫자가 있거나, 제목·요약에 핵심어가 둘 이상"""
        text = 일반경로.normalize_numbers(r["요약"] or 일반경로.snippet_of(r["주소"]))
        if text and any(p.search(text) for p in pats):
            return True
        hay = (r["제목"] + " " + text).lower()
        return sum(w.lower() in hay for w in words) >= 2
    out, seen = [], set()
    for r in rows:
        u = r["주소"]
        if not u or u in seen:
            continue
        seen.add(u)
        is_cited = any(일반경로.on_domain(u, d) for d in cited)
        if (is_cited or (일반경로.trusted_source(u, domain, names, sentence) and not 일반경로.is_news_doc(u))) and relevant(r):
            out.append({"제목": (r["제목"] or u)[:80], "주소": u})
        if len(out) >= MAX_SPOTS:
            break
    return out


def _guard_news(result: dict) -> dict:
    """근거 문서가 언론 기사면 '원문 확인'으로 보지 않는다. 기사가 그 매체를 출처로 밝힌 경우만 예외
    ('FT가 보도', 'WSJ에 따르면' — 분해한 기관 누리집이 그 매체 주소)."""
    if result.get("판정") not in FOUND | {NEAR}:
        return result
    url, path = result.get("상세페이지") or "", result.get("PDF") or ""
    text = ""
    if path.endswith(".txt") and os.path.exists(path):
        text = open(path, encoding="utf-8", errors="ignore").read()
    if not 일반경로.is_news_doc(url, text):
        return result
    domain = 일반경로.clean_domain((result.get("추출") or {}).get("기관누리집", ""))
    flat_in, flat_doc = squash(result.get("입력", "")), squash(text)
    # 입력 문장의 25글자 조각이 기사에 그대로 있으면 같은 기사·베낀 기사(검색 요약은 문장 중간부터라 앞부분만 보면 놓침)
    copied = bool(text) and any(flat_in[i:i + 25] in flat_doc for i in range(0, max(1, len(flat_in) - 25), 10))
    cited = any(일반경로.on_domain(url, d) for d in 일반경로.cited_outlet_domains(result.get("입력", "")))
    if ((domain and 일반경로.on_domain(url, domain)) or cited) and not copied:
        return result   # 기사가 출처로 밝힌 매체의 기사 자체(외신 보도가 1차 공개)
    # 입력 문장이 거의 그대로 든 기사는 같은 기사·베낀 기사다(11/15: 연합인포맥스 시세 기사) → 어떤 예외도 원문 아님
    print(f"    근거가 언론 기사라 원문 확인으로 보지 않음: {url[:80]}")
    keep = {k: v for k, v in result.items() if k in ("입력", "시각", "입구", "출처종류", "추출", "지원밖기관", "시간")}
    return {**keep, "판정": "원출처 미확인", "후보": [{"제목": result.get("보고서"), "주소": url}],
            "이유": "같은 숫자를 쓴 기사는 찾았지만 기관이 낸 원문(보고서·보도자료·공시 등)은 찾지 못했습니다. "
                   "아래 기사는 참고용이며 원문이 아닙니다."}


def _guard_condition(result: dict) -> dict:
    """문장의 숫자가 기준값뿐인데(예: '중위소득의 50%에도 못 미치는') 그 기준값으로 원문을 '확인'했다면 내린다.
    기준값은 정의 문장('상대적 빈곤율이란 중위소득 50% 미만…')에 흔히 있어 주장의 원문이 아니다(11/15 헛답)."""
    cond = result.get("기준값숫자")
    if not cond or result.get("판정") not in FOUND | {NEAR} or result.get("다른숫자로확인"):
        return result
    print(f"    기준값 '{cond}'로만 맞춘 결과라 원문 확인으로 보지 않음")
    found = {"제목": result.get("보고서"), "주소": result.get("상세페이지")}
    keep = {k: v for k, v in result.items() if k in ("입력", "시각", "입구", "출처종류", "추출", "지원밖기관", "시간")}
    return {**keep, "판정": "원출처 미확인", "후보": [found] if found["주소"] else [],
            "이유": (f"문장 속 숫자 '{cond}'는 주장이 아니라 기준값이라, 같은 숫자가 있는 문서를 찾아도 이 주장의 원문이라고 "
                   "볼 수 없습니다. 주장(예: '3명 중 1명')의 원 통계를 직접 확인해 주세요.")}


def _run_once(result: dict, sentence: str, context: str, article_date: str, force: bool,
              prefer: dict | None = None, extra: list | None = None) -> dict:
    with 기록.step("입구 필터"):
        gate = {"통과": True} if force else 입구.check(sentence)
    result["입구"] = gate
    if not gate["통과"]:
        print(f"[0] 입구 필터: {gate.get('종류')} — {gate['이유']}")
        verdict = "확인 대상 아님" if gate.get("종류") == "확인 대상 아님" else "원출처 미확인"
        result.update(판정=verdict, 이유=f"{gate.get('경로') or gate.get('종류')}: {gate['이유']}",
                      후보=gate.get("링크", []))
        return result
    source_kind = 출처종류.classify(sentence)
    result["출처종류"] = source_kind
    if source_kind == "의원실 자료":  # 공개 원문이 없을 수 있음 → 찾지 않고 이유를 밝힘
        print(f"[0] 출처 종류: {source_kind}")
        result.update(판정="원출처 미확인", 이유=f"{source_kind}: {출처종류.MESSAGES[source_kind]}", 후보=[])
        return result
    with 기록.step("문장 분해"):
        info = stabilize(extract_cached(sentence, context, article_date), sentence, article_date)
    if is_condition_number(sentence, info["숫자"]):
        # 기준값을 고른 경우: 문장 속 다른 주장 숫자로 바꾼다. 없으면 기준값만으로는 '원문 확인'을 내지 않는다(_judge 뒤)
        claims = [n for n in all_numbers(sentence) if not is_condition_number(sentence, n)]
        if claims:
            print(f"    고른 숫자 '{info['숫자']}'는 기준값이라 주장 숫자 '{claims[0]}'로 바꿈")
            info = {**info, "숫자": claims[0]}
        else:
            result["기준값숫자"] = info["숫자"]
    result["추출"] = info
    print(f"[1] 라이너 모델 분해: {info}")
    기록.emit("분해", info)

    if prefer:  # 사람이 고른 보고서만 대조(Squash 의 편집자 선택처럼: 자동으로 고른 보고서가 틀렸을 때 바로잡기)
        return _run_preferred(result, sentence, context, article_date, info, prefer)
    inst = normalize_institution(info["기관"])
    # 외국 기관이면 원어 검색어도 순위 기준에 넣는다(11/14: 일본 임야청 백서 쪽이 한국어 기준으로는 순위가 낮았음)
    rank_query = " ".join(x for x in [info.get("보고서", ""), *info["핵심어"], info.get("기준시점", ""),
                                      info.get("원어검색어", "")] if x)
    with 기록.step("검색"), ThreadPoolExecutor(2) as ex:
        # 기관 발간 목록(미리 모아 둔 것)에서 먼저 찾고, 라이너 검색은 동시에(목록에 없을 때 대비)
        listed_f = ex.submit(발간목록.find, inst, info, f"{sentence} {rank_query}")
        site_results, general = search_all(info, inst, is_recent(article_date))
        listed = listed_f.result()
    lead = [{"제목": r.get("title"), "주소": r.get("url")}
            for r in general if not SKIP_LEAD.search(r.get("url", ""))][:5]
    result["_검색링크"] = lead   # 시간 초과 때 보여 줄 것(후보를 고르기 전이면 검색 결과라도)
    result["_검색결과"] = general   # 2라운드: 같은 숫자를 다룬 기사들의 제목·요약에서 기관·보고서를 다시 뽑을 때
    result["_기관검색결과"] = site_results
    finder = FINDERS.get(inst)
    research = 논문찾기.is_research(sentence) or (result.get("입구") or {}).get("경로") == "학술지"
    # 기관을 몰라도 일반 경로로 찾는다(숫자·핵심어 검색에서 나온 공공기관 문서를 대조). 일반 경로는 '원문에 없음'을 내지 않아 안전
    if finder is None:
        # 일반 경로(기본): 어떤 기관이든 공식 누리집의 PDF·웹 페이지를 찾아 같은 방식으로 대조
        finder = 일반경로.finder
        result["지원밖기관"] = info["기관"] or ("학술 논문" if research else "기관 모름")
        with 기록.step("검색"):
            general = general + 일반경로.search(info)
        lead = _lead_links(info, general, lead)
        eng = 일반경로.english_keywords(info)
        if eng:  # 외국 원문(영문)에서도 핵심어를 확인할 수 있게(검색은 이미 끝남)
            info = {**info, "핵심어": [*info["핵심어"], *eng]}
    tokens = period_tokens(info["기준시점"])
    with 기록.step("후보 찾기"):
        found = finder(info, site_results + general)
        # 전용 출처(정책브리핑·DART·여론조사심의위)에서 찾은 후보는 제목이 기사와 달라도 꼭 받는다
        # (11/14: 문체부 보도자료 제목 '케이-컬처…'가 제목 순위에서 밀려 안 받음)
        priority: list[str] = _snippet_number_hits(found, sentence)
        if extra:   # 라이너 Deep Research 가 지목한 원 출처(우리가 받아서 확인) — 꼭 받는다
            found = list(extra) + [c for c in found if c.file_key not in {e.file_key for e in extra}]
            priority = [c.file_key for c in extra] + priority
        if finder is 일반경로.finder and 정책브리핑.is_government(info):
            # 정부 부처·청 보도자료는 라이너 검색에 거의 안 잡힌다 → 정책브리핑 자체 검색(본문 + 첨부 HWP·PDF)
            brief = 정책브리핑.search(info, article_date)
            # 정책브리핑 결과는 제목에 기사 핵심어 낱말이 있을 때만 우선(핵심어 하나로 찾으면 엉뚱한 보도자료도 섞임)
            words = {w for k in info["핵심어"] for w in re.findall(r"[가-힣]{2,}", k)}
            priority += [c.file_key for c in brief if any(w in c.title for w in words)][:MAX_PRIORITY_EACH]
            found = brief + found
        if finder is not 일반경로.finder:
            # 세 기관 전용 경로도 일반 경로 후보를 함께(전용 코드는 보조 장치). 11/14: '보사연'이 나온 문장의 실제 원문이
            # 국무조정실 '청년의 삶 실태조사' 발표였는데 보사연 누리집만 찾아 놓침
            keys = {c.file_key for c in found}
            found = found + [c for c in 일반경로.finder(info, site_results + general) if c.file_key not in keys]
        if finder is 일반경로.finder:
            # 기업 실적·사업 숫자 → DART 분기·사업보고서·잠정실적 원문(공시찾기.py)
            dart = 공시찾기.search(info, sentence, article_date)
            priority += [c.file_key for c in dart[:MAX_PRIORITY_EACH]]
            found = dart + found
        if finder is 일반경로.finder:
            # 닐슨코리아 시청률 → 그날 일일 순위 화면(시청률찾기.py)
            ratings = 시청률찾기.search(sentence, article_date)
            priority += [c.file_key for c in ratings]
            found = ratings + found
        if finder is 일반경로.finder:
            # 여론조사 → 중앙선거여론조사심의위 등록 결과분석 PDF(여론조사찾기.py)
            polls = 여론조사찾기.search(info, sentence, context, article_date)
            priority += [c.file_key for c in polls[:MAX_PRIORITY_EACH]]
            found = polls + found
        if finder is 일반경로.finder and research:
            # 논문 숫자는 초록에 있다 → PubMed 초록을 후보 앞에
            found = 논문찾기.search(info) + found
    keys = {c.file_key for c in listed}
    cands = listed + [c for c in found if c.file_key not in keys]   # 발간 목록 후보가 앞
    cands = drop_rebuttals(cands)   # 보도 설명자료·해명 게시물은 원 보고서가 아님
    print(f"[2] {inst} 원 보고서 후보 {len(cands)}건")
    result["_진행후보"] = [{"제목": c.title, "주소": c.page_url} for c in cands[:LEAD_LINKS]]   # 시간 초과 때 보여 줄 것
    기록.emit("후보", [c.title for c in cands[:MAX_DOWNLOAD]])
    with 기록.step("PDF 받기·글자"):
        docs, why_empty = load_docs(cands, tokens, info.get("보고서", ""), rank_query, article_date,
                                    f"{sentence} {context} {info['기관']}", keep={c.file_key for c in listed} | set(list(dict.fromkeys(priority))[:MAX_PRIORITY]))
    if not docs:
        if mentions_raw_data(sentence):  # 보도자료를 못 찾아도 기사가 통계표 인용을 밝혔으면 그렇게 분류
            return raw_data_result(result, info)
        result.update(판정="원출처 미확인", 이유=why_empty, 후보=lead)
        return result
    result["기준시점확인"] = (f"{docs[0].period}/{len(tokens)}" if tokens else "기사에 기준 시점 없음")
    if info.get("보고서") and not any(named(d) for d in docs):
        result["제목확인"] = (f"기사가 밝힌 보고서 '{info['보고서']}'를 찾지 못해, 가장 가까운 후보로 확인했습니다.")
    기록.emit("보고서", docs[0].cand.title)
    base = dict(result)
    judged = _other_numbers(_judge(result, sentence, info, docs), base, sentence, info, docs)
    return _with_alternatives(judged, docs, info)


FOUND = {"일치", "표현 바뀜", "기사가 계산", "숫자 다름"}
NEAR = "표현 다름 후보"   # 같은 숫자인데 기사가 지표 표현만 바꾼 원문(사람 확인 필요)


def _number_pages(doc: Doc, numbers: list[str], info: dict) -> list[dict]:
    tokens = period_tokens(info.get("기준시점", ""))
    out = []
    for n in numbers:
        hits = find_candidates(doc.pages, n, info["핵심어"], doc.layout, tokens)
        out.append({"숫자": n, "쪽": hits[0]["쪽"] if hits else None})
    return out


def _other_numbers(result: dict, base: dict, sentence: str, info: dict, docs: list[Doc]) -> dict:
    """한 문장에 숫자가 여럿일 때(A4, 11/14).
    - 고른 숫자로 원문을 못 찾았으면, 같은 후보 문서에 나오는 다른 숫자로 다시 대조한다
      (중간 점검 #5: '73만 6,000톤·70만 9,000톤·3.9%' 중 3.9%를 골라 '원문에 없음')
    - 원문을 찾았으면 문장 속 숫자 하나하나가 그 원문에 있는지(쪽)를 붙인다(일부러 바꾼 두 번째 숫자 잡기)"""
    numbers = all_numbers(sentence)
    # 조건·분모 숫자('200달러 이하', '3명 중', '10만 명당')는 주장이 아니다 → 다시 대조에 쓰지 않음
    # (11/14: FEC '200달러 이하'로 일반 설명 페이지와 맞춰 '일치')
    others = [n for n in numbers if n.replace(" ", "") != info["숫자"].replace(" ", "")
              and not is_condition_number(sentence, n)]
    # 흔한 작은 숫자('4건')보다 자릿수가 많은 숫자부터(우연히 같은 숫자를 맞출 위험이 적다)
    others.sort(key=lambda n: -len(re.sub(r"\D", "", n)))
    if result.get("판정") not in FOUND:
        tokens = period_tokens(info.get("기준시점", ""))
        tried = 0
        for n in others:
            if not any(find_candidates(d.pages, n, info["핵심어"], d.layout, tokens) for d in docs):
                continue
            print(f"    문장 속 다른 숫자 '{n}'가 후보 문서에 있어 그 숫자로 다시 대조")
            tried += 1
            if tried > MAX_OTHER_NUMBERS:
                break
            retry = _judge(dict(base), sentence, {**info, "숫자": n}, docs, retry=True)
            firm = retry.get("판정") in FOUND
            # 확정 답이면 바로 쓰고, '표현 다름 후보'는 아직 아무 후보도 없을 때만 쓴다(더 확실한 숫자를 계속 찾음)
            if firm or (retry.get("판정") == NEAR and result.get("판정") != NEAR):
                retry["다른숫자로확인"] = {"처음고른숫자": info["숫자"], "확인한숫자": n}
                retry["추출"] = {**info, "숫자": n}   # 화면 형광펜·대조표가 확인한 숫자를 쓰게
                result = retry
                if firm:
                    break
    if result.get("판정") in FOUND and len(numbers) > 1:
        doc = next((d for d in docs if str(d.pdf) == result.get("PDF")), None)
        if doc:
            result["숫자별확인"] = _number_pages(doc, numbers, info)
    return result


ALTERNATIVES = 3   # 함께 보여 줄 다른 후보 보고서 수
MAX_OTHER_NUMBERS = 3   # 문장 속 다른 숫자로 다시 대조할 최대 개수(자릿수 많은 것부터)
LEAD_LINKS = 5     # 원출처 미확인일 때 보여 줄 후보 링크 수


def _lead_links(info: dict, results: list[dict], lead: list[dict]) -> list[dict]:
    """원출처 미확인일 때 보여 줄 링크: 기관 누리집 안 결과를 맨 앞에, 그다음 기존 일반 검색 결과."""
    domain = 일반경로.clean_domain(info.get("기관누리집", ""))
    own = [{"제목": r.get("title"), "주소": r.get("url")} for r in results if 일반경로.on_domain(r.get("url", ""), domain)]
    out, seen = [], set()
    for x in own + lead + [{"제목": r.get("title"), "주소": r.get("url")} for r in results
                           if not SKIP_LEAD.search(r.get("url", ""))]:
        if x["주소"] and x["주소"] not in seen:
            seen.add(x["주소"])
            out.append(x)
    return out[:LEAD_LINKS]


def _with_alternatives(result: dict, docs: list[Doc], info: dict) -> dict:
    """고르지 않은 후보 보고서(최대 ALTERNATIVES 개)와 그 안에서 기사 숫자가 나온 쪽을 붙인다.
    확신이 낮으면(제목 못 찾음·원문에 없음·교차 확인 경고) 화면이 이 목록을 펼쳐 사람이 고르게 한다."""
    chosen = result.get("PDF")
    alts = []
    for d in docs:
        if str(d.pdf) == chosen or len(alts) >= ALTERNATIVES:
            continue
        hits = find_candidates(d.pages, info["숫자"], info["핵심어"], d.layout, period_tokens(info.get("기준시점", "")))
        alts.append({"제목": d.cand.title, "상세페이지": d.cand.page_url, "PDF주소": d.cand.pdf_url,
                     "키": d.cand.file_key, "숫자쪽": hits[0]["쪽"] if hits else None})
    result["다른후보"] = alts
    result["확신낮음"] = bool(result.get("제목확인") or result.get("판정") == "원문에 없음"
                          or (result.get("교차확인") or {}).get("사람확인권장"))
    return result


def _run_preferred(result: dict, sentence: str, context: str, article_date: str, info: dict, prefer: dict) -> dict:
    """사람이 고른 보고서 하나로만 대조한다(검색 단계 없음)."""
    cand = Candidate(prefer["제목"], prefer.get("상세페이지", ""), prefer["PDF주소"], prefer["키"])
    tokens = period_tokens(info["기준시점"])
    with 기록.step("PDF 받기·글자"):
        docs, why_empty = load_docs([cand], tokens, info.get("보고서", ""), sentence, article_date,
                                    f"{sentence} {context} {info['기관']}")
    result["사람이고른보고서"] = prefer["제목"]
    if not docs:
        result.update(판정="원출처 미확인", 이유=f"고른 보고서를 대조하지 못함: {why_empty or 'PDF 받기 실패'}")
        return result
    result["기준시점확인"] = (f"{docs[0].period}/{len(tokens)}" if tokens else "기사에 기준 시점 없음")
    return _judge(result, sentence, info, docs)


def _confirmed_source(result: dict, info: dict, best: Doc, same_edition: bool, same_kind: bool) -> bool:
    """'원문에 없음'을 말해도 될 만큼 이 문서가 기사가 인용한 원 자료라고 확인됐는지.
    - 기사가 밝힌 보고서 이름과 같은 문서, 또는 기준 시점이 모두 맞는 판이면 확인
    - 일반 경로는 기사가 밝힌 이름과 같은 문서 + 그 기관 누리집 안의 문서여야 확인"""
    # 연도만 맞는 것은 판 확인이 아니다(2020이 든 문서는 많다 — 11/14 중간 점검 #12 인공임신중절 → 엉뚱한 보사연 보고서)
    fine_period = {"월", "분기"} & set(period_tokens(info.get("기준시점", "")))
    edition_ok = bool(named(best)) or (same_edition and bool(fine_period))
    if region_penalty(best.cand.title, result.get("입력", "")):
        return False   # 기사에 없는 지역의 지역판으로는 '원문에 없음'을 말하지 않는다(11/14 회귀: 수도권 시도별판)
    if result.get("지원밖기관"):
        # 일반 경로는 '원문에 없음'을 내지 않는다. 처음 보는 기관의 문서가 그 판인지·소개 페이지인지 코드로 확신할 수 없다
        # (11/14: 다운디텍터 회사 소개, 시점만 맞는 KDI 문서, LG이노텍 3분기 보도자료, 문장만 넣었을 때 AP VoteCast 소개 페이지)
        return False
    # 세 기관 경로도 같은 규칙: '제목에 핵심어가 있음'만으로 '원문에 없음'이 맞았던 사례가 개발 세트에 0건,
    # 틀린 보고서로 '원문에 없음'은 여럿(중간 점검 #2·#12) → 이름·판이 확인될 때만
    return edition_ok


def _same_period(pick: dict | None, doc: Doc, info: dict) -> dict | None:
    """기사에 월·분기 기준 시점이 있는데 모델이 원문 시점을 '다름'으로 보고, 문서 판도 그 시점이 아니면 버린다.
    숫자만 같은 다른 달 통계다(11/14: 10월 생산자물가 6.5%에 5월 자료 6.5%를 '표현 바뀜'으로 댐)."""
    if not pick:
        return pick
    tokens = period_tokens(info.get("기준시점", ""))
    axis = (pick.get("칸") or {}).get("시점") or {}
    if ({"월", "분기"} & set(tokens)) and axis.get("판단") == "다름" and doc.period < len(tokens):
        print(f"    원문 시점이 기사({info.get('기준시점')})와 달라 버림: 원문 '{axis.get('원문', '')}'")
        return None
    return pick


NEAR_DOCS = 2   # '표현 다름 후보'를 물어볼 문서 수(숫자 후보가 나온 앞 문서부터)


def _near(sentence: str, number: str, hits: list[dict], doc: Doc, info: dict) -> dict | None:
    """같은 지표 후보가 없을 때 '표현만 다른 같은 숫자'를 묻는다. 기사에 월·분기가 있는데 문서가 그 판이
    아니면 묻지 않는다(다른 달 같은 숫자 — 11/14 BLS 5월 6.5%)."""
    tokens = period_tokens(info.get("기준시점", ""))
    if ({"월", "분기"} & set(tokens)) and doc.period < len(tokens):
        return None
    try:
        near = near_match(sentence, number, hits)
    except RuntimeError as e:  # 실패하면 후보 없이 계속(이유 출력)
        print(f"    표현 다름 후보 확인 실패: {e}")
        return None
    if near:
        print(f"    표현 다름 후보: {doc.pdf.name} {near['후보']['쪽']}쪽 — 기사 '{near['기사표현']}' ↔ 원문 '{near['원문표현']}'")
        near["문서"] = doc
    return near


def near_result(result: dict, near: dict, number: str) -> dict:
    """'표현 다름 후보' 결과: 원문 문장에 같은 숫자가 있지만 기사와 지표 표현이 다르다(사람이 확인할 것)."""
    doc, hit = near["문서"], near["후보"]
    result.update(판정="표현 다름 후보", 보고서=doc.cand.title, 상세페이지=doc.cand.page_url, PDF=str(doc.pdf),
                  쪽=hit["쪽"], 찾은표기=hit["표기"], 표기종류=hit["종류"], 원문인용=hit["원문"],
                  표현차이={"기사": near["기사표현"], "원문": near["원문표현"]},
                  찾은방법="글자 찾기 + 라이너 모델(같은 숫자인데 기사가 표현만 바꾼 원문, 두 번 물어 같은 답일 때만)",
                  이유=(f"원문에 같은 숫자({hit['표기']})가 있지만 표현이 다릅니다: 기사 '{near['기사표현']}' ↔ "
                       f"원문 '{near['원문표현']}'. 같은 내용인지 아래 원문을 직접 확인해 주세요."))
    if 일반경로.is_snippet(doc.pdf):
        result["확인못한부분"] = "원문 페이지를 직접 열지 못해 검색 요약 글로 확인했습니다. 링크를 열어 직접 확인해 주세요."
    기록.emit("판정", dict(result))
    return result


def _safe_calc(pages: list[str], sentence: str, info: dict) -> dict:
    """'기사가 계산'인지: 코드가 원문 숫자 조합을 모두 계산하고 라이너 모델은 고르기만(계산찾기.py)."""
    try:
        return 계산찾기.check(pages, sentence, info)
    except RuntimeError as e:  # 계산 확인 실패는 '계산 안 됨'으로 두고 이유를 남김
        print(f"    계산 확인 실패: {e}")
        return {"계산됨": False, "버린이유": f"계산 확인 실패: {e}"}


MAX_SNIPPET_PRIORITY = 3


def _snippet_number_hits(cands: list, sentence: str) -> list[str]:
    """검색 요약 글에 기사 문장의 숫자(아무것이나)가 든 후보 — 제목이 달라도 꼭 받는다
    (11/14 무작위3: 경기도 보도자료 제목 '민생사수예산 편성' 요약에 '4조 595억'이 있었는데 제목 순위에서 밀림)."""
    pats = [to_regex(v.text) for n in all_numbers(sentence) for v in variants(n)]
    out = []
    for c in cands:
        desc = 일반경로.normalize_numbers(일반경로.snippet_of(c.page_url))
        if desc and any(p.search(desc) for p in pats):
            out.append(c.file_key)
        if len(out) >= MAX_SNIPPET_PRIORITY:
            break
    return out


def _judge(result: dict, sentence: str, info: dict, docs: list[Doc], retry: bool = False) -> dict:
    number = info["숫자"]
    with 기록.step("숫자 찾기"):
        found = [(d, find_candidates(d.pages, number, info["핵심어"], d.layout, period_tokens(info.get("기준시점", "")))) for d in docs]
    best = docs[0]
    ex = ThreadPoolExecutor(2)
    gateway_f = calc_f = None

    def start_fallback():
        # 글자 찾기로 못 찾을 때 쓰는 두 길(게이트웨이 직접 읽기, 계산 확인)을 동시에 출발
        nonlocal gateway_f, calc_f
        # 게이트웨이는 PDF 만 읽는다. 다른 숫자로 다시 대조할 때는 안 읽는다(11/15: 숫자 많은 문장에서 같은 PDF 를 4번 읽어 120초)
        if gateway_f is None and not retry and not 일반경로.is_web(best.pdf):
            gateway_f = ex.submit(read_pdf, best.pdf, sentence, number, best.pages, info["핵심어"], best.layout)
        if calc_f is None and not mentions_raw_data(sentence):
            calc_f = ex.submit(_safe_calc, best.pages, sentence, info)

    try:
        if not found[0][1]:
            print(f"[3] {best.pdf.name}: 숫자 후보 0곳 → PDF 직접 읽기를 미리 출발")
            start_fallback()
        judged = 0
        near = None   # '표현 다름 후보': 같은 숫자인데 기사가 지표 표현만 바꾼 원문(끝까지 확정 답이 없을 때 씀)
        for doc, hits in found:
            print(f"[3] {doc.pdf.name}: 숫자 후보 {len(hits)}곳")
            if not hits:
                continue
            with 기록.step("고르기·판정"):
                hits = rank_hits(sentence, hits)
                pick = _same_period(choose(sentence, number, hits, doc.pages), doc, info)
            judged += 1
            if pick:
                기록.emit("쪽", {"PDF": doc.pdf.name, "쪽": pick["후보"]["쪽"], "표기": pick["후보"]["표기"]})
                return found_result(result, doc, pick, "글자 찾기 + 라이너 Rerank 순위 + 라이너 모델 판정",
                                    sentence, number)
            if near is None and judged <= NEAR_DOCS:
                with 기록.step("표현 다름 후보"):
                    near = _near(sentence, number, hits, doc, info)
            if judged >= MAX_DOCS_TO_JUDGE:
                break

        print(f"[4] 글자 찾기로 못 찾음 → 라이너 게이트웨이 모델이 {best.pdf.name} 직접 읽기(계산 확인도 동시에)")
        start_fallback()
        with 기록.step("PDF 직접 읽기"):
            hit = gateway_f.result() if gateway_f else None
        if hit:
            with 기록.step("고르기·판정"):
                pick = _same_period(choose(sentence, number, [hit], best.pages), best, info)
            if pick:
                기록.emit("쪽", {"PDF": best.pdf.name, "쪽": hit["쪽"], "표기": hit["표기"]})
                return found_result(result, best, pick, "PDF 직접 읽기(라이너 게이트웨이) + 원문 대조",
                                    sentence, number)

        if near:
            return near_result(result, near, number)
        result.update(보고서=best.cand.title, 상세페이지=best.cand.page_url, PDF=str(best.pdf))
        if mentions_raw_data(sentence):
            return raw_data_result(result, info)
        with 기록.step("계산 확인"):
            calc = calc_f.result() if calc_f else _safe_calc(best.pages, sentence, info)
        fine = {"월", "분기"} & set(period_tokens(info.get("기준시점", "")))
        if calc.get("계산됨") and fine and best.period < len(period_tokens(info.get("기준시점", ""))):
            # 기사에 월·분기가 있는데 다른 판 문서의 숫자로 계산이 맞은 것은 우연(11/14: 11월 갤럽 기사에 6월 보고서 497+505=1002)
            print("    계산은 맞지만 문서가 기사 기준 시점의 판이 아니라 버림")
            calc = {"계산됨": False, "버린이유": "다른 판 문서의 숫자 계산"}
        # '원문에 없음'은 찾아본 범위를 함께 밝힌다(기사가 틀렸다는 뜻으로 읽히지 않게)
        result.update(판정="기사가 계산" if calc.get("계산됨") else "원문에 없음", 계산확인=calc,
                      확인한문서=[d.cand.title for d in docs])
        tokens = period_tokens(info.get("기준시점", ""))
        # '숫자 다름'은 맞는 기사를 틀렸다고 할 위험이 가장 큰 판정 → 문서가 기사와 같은 판이라고 확실할 때만.
        # 기준 시점이 있으면 연·월·분기가 모두 맞는 판, 없으면 기사가 밝힌 보고서 이름과 같은 문서.
        # (11/13 시험: 다른 달 산업활동동향을 골라 맞는 3.0% 기사에 '원문은 0.3% 감소'라고 함)
        same_edition = (best.period == len(tokens)) if tokens else bool(named(best))
        # 자료 성격도 같아야 한다: 기사가 밝힌 이름의 핵심 낱말이 문서 제목에 모두 있거나(이름이 있을 때),
        # 기사 핵심어가 문서 제목에 하나라도 있어야(이름이 없을 때). '조생양파 재배면적' 기사에
        # '보리·마늘·양파 생산량조사'의 숫자를 근거로 대던 것 막기(11/13 오보 사례 #1)
        same_kind = (missing_nouns(info["보고서"], best.cand.title) == 0 if info.get("보고서") else
                     any(w in best.cand.title for k in info["핵심어"] for w in re.findall(r"[가-힣A-Za-z]{2,}", k)))
        if result["판정"] == "원문에 없음" and not _confirmed_source(result, info, best, same_edition, same_kind):
            # 맞는 문서인지 확인 못 한 채 '원문에 없음'이라 하면 사용자는 기사가 틀렸다고 읽는다
            # (11/14 무작위 30건: 엉뚱한 보고서를 고르고 '원문에 없음' 8건) → 정직하게 '원출처 미확인'
            why = (f"기사가 밝힌 자료와 이름이 같은 문서({best.cand.title[:40]})를 찾았지만 기사 숫자가 없었습니다. "
                   "다른 판이거나 소개 페이지일 수 있어 '원문에 없음'으로 판단하지 않았습니다."
                   if named(best) else
                   f"찾아본 문서 {len(docs)}건에서 기사 숫자를 찾지 못했습니다. 이 문서들이 기사가 인용한 원 자료라고 "
                   "확인되지 않아 '원문에 없음'으로 판단하지 않았습니다.")
            result.update(판정="원출처 미확인", 이유=why + " 아래 후보 링크를 직접 확인해 주세요.",
                후보=[{"제목": d.cand.title, "주소": d.cand.page_url} for d in docs[:LEAD_LINKS]])
            return result
        if result["판정"] == "원문에 없음" and not same_edition:
            print("    같은 판인지 확실하지 않아 '숫자 다름'은 확인하지 않음")
        if result["판정"] == "원문에 없음" and same_edition:
            with 기록.step("같은 지표 다른 숫자"):
                other = (다른값찾기.find_in_image_pdf(best.pdf, sentence, number) if 다른값찾기.is_image_pdf(best.pages)
                         else 다른값찾기.find(best.pages, sentence, number, info["핵심어"]))
            check = same_indicator(sentence, other["원문인용"]) if other and same_kind else None
            if other and check is not None:
                other["교차확인"] = check
            if other and (not same_kind or not check["통과"]):  # 자료 성격이 다르거나 Decisions 가 다른 지표로 봄 → 참고로만
                result["참고값"] = {**other, "안내": "같은 지표로 보이는 원문 값이지만, 기사가 인용한 자료와 다른 자료일 수 있어 참고로만 보여 줍니다."}
            elif other:  # 원문에 같은 지표·대상·시점의 다른 숫자가 있음 → 기사 숫자와 다름을 근거와 함께
                result.update(판정="숫자 다름", 쪽=other["쪽"], 원문인용=other["원문인용"], 원문숫자=other["원문숫자"],
                              칸별비교={k: {"판단": v} for k, v in other["칸"].items()},
                              교차확인={"판정": "숫자 다름", "확신도": check["같은지표확률"], "같음": True,
                                    "다른축": [], "사람확인권장": False},
                              찾은방법="원문에서 같은 지표·대상·시점의 숫자를 찾음(라이너 모델 + 원문 글자 대조)")
                if other.get("검증"):
                    result.update(찾은방법="그림 PDF를 라이너 게이트웨이 모델이 직접 읽어 같은 지표의 숫자를 찾음",
                                  확인못한부분=other["검증"])
        return result
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def show(r: dict) -> None:
    print(f"\n━━ 판정: {r['판정']} ━━")
    if r.get("보고서"):
        print(f"원 보고서: {r['보고서']}\n  {r['상세페이지']}  (기준시점 확인 {r.get('기준시점확인', '-')})")
    if r["판정"] in ("일치", "표현 바뀜"):
        print(f"쪽: PDF {r['쪽']}쪽 · 찾은 표기 '{r['찾은표기']}' ({r['표기종류']}) · {r['찾은방법']}")
        print(f"원문: {r['원문인용']}")
        for d in r["차이"]:
            print(f"  · [{d.get('항목')}] 기사 '{d.get('기사')}' ↔ 원문 '{d.get('원문')}'")
        if r.get("확인못한부분"):
            print(f"  · 원문으로 확인 못 한 부분: {r['확인못한부분']}")
    elif r["판정"] == "기사가 계산":
        calc = r["계산확인"]
        print(f"계산식: {calc.get('식')} ({calc.get('쪽')}쪽)")
        for m in calc.get("재료", []):
            print(f"  · 재료 {m.get('숫자')}: {m.get('원문')}")
    elif r["판정"] == "통계표·원자료 인용":
        print(r["이유"])
        for t in r.get("통계표후보", []):
            print(f"  · 통계표 후보: {t['제목']} {t['주소']}")
    elif r["판정"] == "원문에 없음":
        print("원 보고서에서 숫자를 찾지 못했고, 원문 숫자로 계산되지도 않음")
    else:
        print(f"이유: {r['이유']}")
        for h in r.get("후보", []):
            print(f"  · 후보: {h['제목']} {h['주소']}")


def save(r: dict) -> Path:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULT_DIR / f"{datetime.now():%Y%m%d_%H%M%S_%f}.json"
    path.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('사용: python3 도구/원문찾기.py "숫자가 든 기사 문장" ["기사 문맥"] ["기사 날짜"]')
    res = run(*sys.argv[1:4], force=True)
    show(res)
    print(f"\n결과 저장: {save(res)}")
