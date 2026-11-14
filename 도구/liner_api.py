"""Liner API 호출 모음 (웹 검색, 모델, Rerank, Decisions). 표준 라이브러리만 사용.

키는 프로젝트 폴더의 .env 파일에서 LINER_API_KEY 를 읽는다.
11/13 시험으로 확인한 것:
- reasoning_effort(생각하는 양)는 라우터·게이트웨이 Gemini 모두 실제로 적용된다(low 면 생각 토큰 0, 몇 초 빠름).
- response_format(json_schema)도 적용된다. temperature·seed 는 적용 안 됨(라이너 문서).
- 일반 모델은 동시 10건도 바로 답함. 게이트웨이는 동시 10건이면 줄을 서서 느려짐 → 동시 GATEWAY_PARALLEL 건으로 제한.
"""
import base64
import hashlib
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import 기록
import 캐시

BASE_URL = "https://platform.liner.com/api/v1"
MODEL = "liner-gpt-router"
PDF_MODEL = "google/gemini-3.8-flash"  # 라이너 게이트웨이에서 PDF 파일을 읽는 모델
PDF_MAX_BYTES = 14 * 1024 * 1024       # base64 로 늘면 약 19MB (요청당 20MB 제한, 라이너 문서)
RERANK_MODEL = "cohere/rerank-v3.5"    # 후보 보고서·숫자 후보 순위 매기기
RERANK_DOC_CHARS = 1500
DECISION_MODEL = "typesafe/jev-1.13"   # 판정 교차 확인(선택지별 확률)
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
TIMEOUT_SEC = 180
RETRY_WAITS_SEC = [3, 10]              # 429·5xx·시간 초과면 쉬었다 다시(Retry-After 가 있으면 그 값)
GATEWAY_PARALLEL = 3                   # 게이트웨이(PDF 읽기) 동시 호출 수
RERANK_PARALLEL = 5                    # Rerank·Decisions 계정당 동시 5건(라이너 문서)
MAX_OUTPUT_TOKENS = 2000

_gateway_slots = threading.Semaphore(GATEWAY_PARALLEL)
_rerank_slots = threading.Semaphore(RERANK_PARALLEL)
_no_limit = threading.Semaphore(100)


def _api_key():
    key = os.environ.get("LINER_API_KEY")
    if not key and ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("LINER_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        raise RuntimeError(".env 파일에 LINER_API_KEY 가 없습니다.")
    return key


def _post(path, body, headers, slots=_no_limit):
    """POST 후 JSON. 429·5xx·시간 초과는 RETRY_WAITS_SEC 만큼 쉬고 다시 시도한다."""
    data = json.dumps(body).encode("utf-8")
    for wait in [*RETRY_WAITS_SEC, None]:
        req = urllib.request.Request(BASE_URL + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json", **headers})
        try:
            with slots, urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if wait is None or not (e.code == 429 or e.code >= 500):
                raise RuntimeError(f"Liner API 실패({path}, HTTP {e.code}): {detail}") from e
            after = e.headers.get("Retry-After")
            wait = float(after) if after and after.replace(".", "").isdigit() else wait
            print(f"    (라이너 {path} HTTP {e.code}, {wait:.0f}초 뒤 다시 시도)")
        except (TimeoutError, urllib.error.URLError) as e:
            if wait is None:
                raise RuntimeError(f"Liner API 연결 실패({path}): {e}") from e
            print(f"    (라이너 {path} 연결 문제 {e}, {wait}초 뒤 다시 시도)")
        time.sleep(wait + random.uniform(0, 1))
    raise AssertionError("도달하지 않음")


def web_search(query, max_results=10, lang="ko", date_range=None):
    """라이너 웹 검색. 결과 목록(title, url, description, date)을 돌려준다.
    date_range: None 또는 past_day / past_week / past_month / past_year
    'site:기관도메인', 'filetype:pdf' 연산자가 검색어 안에서 작동한다(11/12 시험).
    Premium Data(include_knowledge)는 기본으로 켜져 비용이 붙고 한국 통계가 없어 끈다.
    같은 검색은 캐시에서(라이너 검색 문서 권장). 최근 조건 검색은 캐시를 짧게."""
    key = f"{query}|{lang}|{max_results}|{date_range}"
    hours = 캐시.RECENT_SEARCH_HOURS if date_range in ("past_day", "past_week") else 캐시.SEARCH_HOURS
    cached = 캐시.get("검색", key, hours)
    if cached is not None:
        return cached
    body = {"query": query, "lang": lang, "max_results": max_results, "include_knowledge": False}
    if date_range:
        body["date_range"] = date_range
    t = time.time()
    data = _post("/tools/search/web", body, {"x-api-key": _api_key()})
    기록.api_call("검색", "liner-search", time.time() - t)
    results = data.get("results")
    if not isinstance(results, list):
        raise RuntimeError(f"웹 검색 응답에 results 가 없습니다: {str(data)[:300]}")
    캐시.put("검색", key, results)
    return results


def scholar_search(query, max_results=10, lang=None):
    """라이너 Scholar 검색(논문). 결과(title, url, description, journal, authors, citationCount). 캐시 사용.
    11/14 시험: 영어 검색어로 넣으면 원 논문을 1위로(전문 PDF 링크까지) 찾고, 한국어 검색어는 엉뚱함."""
    key = f"scholar|{query}|{lang}|{max_results}"
    cached = 캐시.get("검색", key, 캐시.SEARCH_HOURS)
    if cached is not None:
        return cached
    body = {"query": query, "max_results": max_results}
    if lang:
        body["lang"] = lang
    t = time.time()
    data = _post("/tools/search/scholar", body, {"x-api-key": _api_key()})
    기록.api_call("검색", "liner-scholar", time.time() - t)
    results = data.get("results")
    if not isinstance(results, list):
        raise RuntimeError(f"Scholar 검색 응답에 results 가 없습니다: {str(data)[:300]}")
    캐시.put("검색", key, results)
    return results


def rerank(query, documents, top_n=None):
    """라이너 Rerank: 문서 목록을 query 와 관련 높은 순으로. [(원래 위치, 점수), ...] 를 돌려준다."""
    if not documents:
        return []
    body = {"model": RERANK_MODEL, "query": query, "documents": [d[:RERANK_DOC_CHARS] for d in documents]}
    if top_n:
        body["top_n"] = top_n
    t = time.time()
    data = _post("/rerank", body, {"Authorization": f"Bearer {_api_key()}"}, _rerank_slots)
    기록.api_call("Rerank", RERANK_MODEL, time.time() - t)
    try:
        return [(r["index"], float(r["relevance_score"])) for r in data["results"]]
    except (KeyError, TypeError, ValueError) as e:
        raise RuntimeError(f"Rerank 응답 형식이 예상과 다릅니다: {str(data)[:300]}") from e


def decide_many(state, questions):
    """라이너 Decisions: 질문 여러 개를 한 번에(질문끼리 서로의 답을 보지 않음).
    questions: {이름: {"instructions": 설명, "criteria": {선택지: 기준}}}
    돌려주는 값: {이름: {"choice", "probabilities", "confidence"}}"""
    body = {"model": DECISION_MODEL, "state": state,
            "questions": {n: {"type": "choice", **q} for n, q in questions.items()}}
    t = time.time()
    data = _post("/decisions", body, {"Authorization": f"Bearer {_api_key()}"}, _rerank_slots)
    기록.api_call("Decisions", DECISION_MODEL, time.time() - t)
    try:
        return {n: data["answers"][n] for n in questions}
    except (KeyError, TypeError) as e:
        raise RuntimeError(f"Decisions 응답 형식이 예상과 다릅니다: {str(data)[:300]}") from e


def decide(state, name, instructions, criteria):
    """질문 하나짜리 Decisions. 돌려주는 값: {"choice", "probabilities", "confidence"}"""
    return decide_many(state, {name: {"instructions": instructions, "criteria": criteria}})[name]


def json_schema(name: str, properties: dict, required: list[str] | None = None) -> dict:
    """response_format 용 JSON 스키마(strict). name 은 영문·숫자만(한글이면 HTTP 400). properties 는 {"칸": {"type": ...}}."""
    return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": {
        "type": "object", "properties": properties, "required": required or list(properties),
        "additionalProperties": False}}}


def ask_json(prompt, pdf_path=None, *, kind="모델", effort="low", schema=None, routing=None):
    """라이너 모델에 묻고 JSON 답을 dict 로 돌려준다.
    pdf_path 를 주면 PDF 를 읽을 수 있는 게이트웨이 모델(PDF_MODEL)에 파일을 같이 보낸다.
    (라우터 모델 liner-* 는 PDF 를 받지 못함)
    effort: 생각하는 양(none·low·medium…). 라우터 none 은 가벼운 모델이 답함. 게이트웨이 Gemini 는 none 대신 low.
    schema: json_schema(...) 결과. 주면 답 형식을 강제한다.
    routing: 라우터의 cost·balanced·quality(답할 모델 고르는 기준)."""
    if pdf_path is None:
        body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}]}
        if routing:
            body["routing_strategy"] = routing
        slots, file_tag = _no_limit, ""
    else:
        raw = Path(pdf_path).read_bytes()
        if len(raw) > PDF_MAX_BYTES:
            raise RuntimeError(f"PDF가 너무 큽니다({len(raw) // 1024 // 1024}MB > "
                               f"{PDF_MAX_BYTES // 1024 // 1024}MB): {pdf_path}")
        file_data = "data:application/pdf;base64," + base64.b64encode(raw).decode()
        body = {"model": PDF_MODEL, "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "file", "file": {"filename": Path(pdf_path).name, "file_data": file_data}},
        ]}]}
        effort = "low" if effort == "none" else effort
        slots, file_tag = _gateway_slots, hashlib.sha1(raw).hexdigest()
    body["reasoning_effort"] = effort
    body["max_completion_tokens"] = MAX_OUTPUT_TOKENS
    if schema:
        body["response_format"] = schema
    cache_key = json.dumps([body["model"], prompt, file_tag, effort, schema, routing], ensure_ascii=False)
    if 캐시.model_cache_on() and (hit := 캐시.get("모델", cache_key)) is not None:
        return hit
    t = time.time()
    data = _post("/chat/completions", body, {"Authorization": f"Bearer {_api_key()}"}, slots)
    기록.api_call(kind, data.get("model") or body["model"], time.time() - t, data.get("usage"))
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"모델 응답 형식이 예상과 다릅니다: {str(data)[:300]}") from e
    match = re.search(r"\{.*\}", text or "", re.S)  # ```json 감싸기 대비
    if not match:
        raise RuntimeError(f"모델 답에서 JSON 을 찾지 못했습니다: {(text or '')[:300]}")
    try:
        ans = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"모델 답의 JSON 이 깨졌습니다: {match.group(0)[:300]}") from e
    if 캐시.model_cache_on():
        캐시.put("모델", cache_key, ans)
    return ans
