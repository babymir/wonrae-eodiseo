"""라이너 Deep Research 에서 원문 후보 받기(11/15).

검증 세트 비교: 우리가 못 찾은 원문을 Deep Research 가 찾은 경우(하나증권 보고서, Rezolute 보도자료)와
반대 경우(DART 공시 숫자)가 있어 서로 보완한다. 그래서 우리 방식으로 못 찾으면 Deep Research 에 묻고,
답에서 '원 출처'로 지목한 주소만 후보로 받는다. 확인(문서를 직접 받아 숫자 대조·판정)은 우리 코드가 한다
→ Deep Research 가 기사를 원문으로 착각하거나 문장을 지어내도 우리 확인 단계에서 걸러진다.
- API: POST https://platform.liner.com/api/v1/agents/deep-research (stream=false → answer, references)
- 같은 질문의 답은 캐시(작업/캐시/딥리서치)에 저장해 다시 쓴다(비용·흔들림)
- DART 공시 보기 주소(rcpNo=)는 OpenDART 원문으로 받는다(보기 화면은 글자가 비어 있음)
"""
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request

import 일반경로

import 기록
import 캐시
from liner_api import _api_key

URL = "https://platform.liner.com/api/v1/agents/deep-research"
TIMEOUT_SEC = 90
CACHE_HOURS = 24 * 30
MAX_CANDIDATES = 4
QUESTION = ("다음 한국어 기사 문장 속 숫자가 처음 나온 원 출처 문서(보고서·보도자료·공시·통계표·논문 등)를 찾아 줘. "
            "그 문서의 주소와, 그 문서 안에서 숫자가 나온 문장을 그대로 인용해 줘. 기사는 원 출처가 아니다.\n"
            "문장: \"{s}\"{c}")
NEWS_HINT = re.compile(r"news|daum\.net|naver\.com|blog|youtube|wikipedia|namu\.wiki|ppomppu|clien|dcinside")


def ask(sentence: str, context: str = "", timeout: float = TIMEOUT_SEC) -> dict:
    """{'답', '참고': [{제목, 주소}], '초'} 또는 {'오류'}. 같은 문장·문맥이면 저장한 답."""
    ctx = f"\n기사 문맥: \"{context[:600]}\"" if context else ""
    question = QUESTION.format(s=sentence, c=ctx)
    key = hashlib.md5(question.encode()).hexdigest()
    hit = 캐시.get("딥리서치", key, CACHE_HOURS)
    if hit is not None:
        return hit
    body = json.dumps({"messages": [{"role": "user", "content": question}], "lang": "ko", "stream": False}).encode()
    req = urllib.request.Request(URL, data=body, method="POST",
                                 headers={"x-api-key": _api_key(), "Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except Exception as e:  # 실패는 후보 없이 계속(이유 출력)
        print(f"    라이너 Deep Research 실패: {type(e).__name__}: {e}")
        return {"오류": str(e)}
    secs = time.time() - t
    기록.api_call("에이전트", "liner-deep-research", secs)
    out = {"답": data.get("answer", ""), "초": round(secs),
           "참고": [{"제목": x.get("title") or "", "주소": x.get("url") or ""} for x in data.get("references") or []]}
    캐시.put("딥리서치", key, out)
    return out


def named_sources(ans: dict) -> list[dict]:
    """답에서 원 출처로 지목한 주소(본문에 쓴 주소 → 본문 ((n)) 표시가 가리키는 참고 순). 언론·블로그는 뺀다."""
    text, refs = ans.get("답", ""), ans.get("참고", [])
    out, seen = [], set()
    for u in re.findall(r"https?://[^\s)\]>*'\"`]+", text):
        u = u.rstrip(".,;")
        title = next((r["제목"] for r in refs if r["주소"] == u), "") or "라이너 Deep Research가 지목한 원 출처"
        out.append({"제목": title, "주소": u})
    for n in re.findall(r"\(\((\d+)\)\)", text):
        i = int(n) - 1
        if 0 <= i < len(refs):
            out.append(refs[i])
    result = []
    for x in out:
        host = urllib.parse.urlparse(x["주소"]).netloc.lower()   # 주소 경로의 'news'(보도자료 게시판)는 언론이 아님
        if x["주소"] and x["주소"] not in seen and not NEWS_HINT.search(host) and not 일반경로.is_news_or_social(x["주소"]):
            seen.add(x["주소"])
            result.append(x)
    return result[:MAX_CANDIDATES]


QUOTE_LINE = re.compile(r"[>]\s*[“\"]([^”\"]{20,300})[”\"]|[“]([^”]{20,300})[”]")


def quotes(ans: dict) -> list[str]:
    """답에서 원문 인용으로 낸 문장들(> “…” 꼴)."""
    out = []
    for a, b in QUOTE_LINE.findall(ans.get("답", "")):
        q = re.sub(r"\s+", " ", a or b).strip()
        if q and q not in out:
            out.append(q)
    return out[:3]


def snippet_candidates(ans: dict, sources: list[dict], number: str) -> list:
    """자동 접속이 막힌 누리집(BLS·국무부 등) 대비: Deep Research 가 인용한 문장을 라이너 검색으로 따로 찾아,
    그 누리집 결과의 검색 요약에 기사 숫자가 있으면 그 요약을 후보로(Deep Research 말이 아니라 검색 색인으로 확인)."""
    from liner_api import web_search
    from 기관별 import Candidate
    from 숫자표기 import to_regex, variants
    pats = [to_regex(v.text) for v in variants(number)]
    hosts = {일반경로.host(x["주소"]) for x in sources}
    out = []
    for q in quotes(ans):
        try:
            rs = web_search(q[:150], max_results=10)
        except RuntimeError as e:
            print(f"    인용 문장 검색 실패: {e}")
            continue
        for r in rs:
            url, desc = r.get("url") or "", 일반경로.normalize_numbers(str(r.get("description") or ""))
            if 일반경로.host(url) in hosts and any(p.search(desc) for p in pats):
                일반경로.register_snippet(url, str(r.get("description")))
                key = "gen_snip_" + hashlib.sha1(url.encode()).hexdigest()[:12]
                if key not in {c.file_key for c in out}:
                    out.append(Candidate(r.get("title") or url, url, url, key))
    if out:
        print(f"    Deep Research 인용 문장을 라이너 검색으로 다시 찾아 원 출처 검색 요약 {len(out)}건 확보")
    return out

