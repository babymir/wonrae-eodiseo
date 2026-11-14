"""기준선 비교(A8, 11/14): '검색 + AI 답변'(Deep Research·Perplexity 류의 단순형)과 「원래 어디서?」를 같은 문장으로 비교한다.

결선 예상 질문 "라이너 Deep Research 로 되지 않나요?"에 숫자로 답하기 위한 것.
라이너 Deep Research 는 API 가 없어, 자동으로 돌릴 수 있는 기준선을 만든다:
  라이너 검색(문장 그대로, 상위 8건) → 라이너 모델에 "원 출처 주소·쪽·원문 문장"을 묻는다.
그리고 기준선이 준 원문 문장이 그 주소 문서에 정말 있는지 코드로 검사한다(지어낸 인용 찾기).
실제 라이너 Deep Research 비교는 사람이 화면에서 해야 해서 양식(작업/기준선비교/딥리서치_손비교.md)만 만든다.

사용: python3 도구/기준선_비교.py 평가_무작위:3 평가_무작위2:4 ...   (폴더:번호, 우리 결과는 그 폴더의 평가결과.json)
결과: 작업/기준선비교/비교결과.json (한 건마다 저장 → 다시 실행하면 이어서), 비교표.md
"""
import hashlib
import json
import re
import sys
from pathlib import Path

from liner_api import ask_json, json_schema, web_search
from PDF도구 import pdf_pages, quote_fuzzy, quote_in_pages
from 기관별 import Candidate
from 숫자표기 import core_number
from 일반경로 import fetch_doc, is_web, web_pages
from 평가_실행 import article_context

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "작업" / "기준선비교"
SEARCH_TOP = 8
FOUND = {"일치", "표현 바뀜", "기사가 계산", "숫자 다름"}
S = {"type": "string"}
SCHEMA = json_schema("baseline", {"원문주소": S, "쪽": {"type": "integer"}, "원문문장": S, "확신": S})
PROMPT = """너는 리서치 도우미다. 기사 문장 속 숫자가 처음 나온 원 출처 문서(보고서·통계·보도자료·원 자료 페이지)를 찾아라.
아래 웹 검색 결과를 참고하되, 아는 것이 있으면 써도 된다.
기사 문장: "{sentence}"
기사 문맥: "{context}"
검색 결과:
{results}
JSON 하나로만 답하라: {{"원문주소": "원 출처 문서 주소(모르면 \\"\\")", "쪽": PDF면 쪽 번호(모르면 0),
"원문문장": "원 출처에서 그 숫자가 나온 문장을 원문 그대로", "확신": "높음|중간|낮음"}}"""


def baseline(sentence: str, context: str) -> dict:
    try:
        rs = web_search(sentence)[:SEARCH_TOP]
    except RuntimeError as e:  # 검색 실패도 기준선 결과로 남긴다(같은 조건)
        print(f"    기준선 검색 실패: {e}")
        rs = []
    lines = "\n".join(f"{i}. {r.get('title', '')} | {r.get('url', '')} | {str(r.get('description', ''))[:300]}"
                      for i, r in enumerate(rs, start=1))
    return ask_json(PROMPT.format(sentence=sentence, context=context[:1500], results=lines or "(없음)"),
                    kind="기준선", effort="low", schema=SCHEMA)


def verify(ans: dict, number: str) -> tuple[str, str]:
    """(분류, 근거). 원문 문장이 그 주소 문서에 정말 있고 기사 숫자가 들어 있는지."""
    url, quote = (ans.get("원문주소") or "").strip(), (ans.get("원문문장") or "").strip()
    if not url:
        return "답 없음", "원문 주소를 내지 않음"
    if not quote:
        return "링크만", "원문 문장을 내지 않음"
    cand = Candidate(url, url, url, "gen_" + hashlib.sha1(url.encode()).hexdigest()[:12])
    try:
        path = fetch_doc(cand)
    except RuntimeError as e:
        return "확인 불가", f"주소를 열 수 없음: {str(e)[:80]}"
    if path is None:
        return "확인 불가", "PDF·웹 페이지가 아님"
    pages = web_pages(path) if is_web(path) else pdf_pages(path)
    page = quote_in_pages(quote, pages) or quote_fuzzy(quote, pages, number)
    core = core_number(number).rstrip("%~")
    has_number = core.replace(",", "") in re.sub(r"[,\s]", "", quote)
    if page and has_number:
        return "원문 확인", f"인용이 문서 {page}쪽에 있음"
    if page:
        return "인용은 있으나 숫자 없음", f"{page}쪽에 인용은 있지만 기사 숫자가 없음"
    return "지어낸 인용", "그 주소 문서에서 인용 문장을 찾지 못함"


def ours(r: dict) -> tuple[str, str]:
    res = r.get("결과", {})
    v = res.get("판정", "오류")
    if v in FOUND:
        where = f"p{res['쪽']}" if res.get("쪽") else "계산식" if v == "기사가 계산" else ""
        return "원문 확인", f"{v} {res.get('보고서', '')[:30]} {where}"
    links = (res.get("안내") or {}).get("링크") or res.get("통계표후보") or res.get("후보") or []
    return ("링크·안내" if links else "못 찾음(정직)"), f"{v}" + (f" — {(res.get('안내') or {}).get('유형', '')}" if res.get("안내") else "")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    store = OUT / "비교결과.json"
    done = json.loads(store.read_text(encoding="utf-8")) if store.exists() else {}
    for item in sys.argv[1:]:
        folder, no = item.split(":")
        targets = {t["번호"]: t for t in json.loads((ROOT / "작업" / folder / "평가대상.json").read_text(encoding="utf-8"))}
        results = {r["번호"]: r for r in json.loads((ROOT / "작업" / folder / "평가결과.json").read_text(encoding="utf-8"))}
        t, r = targets[int(no)], results.get(int(no), {})
        if item in done:
            continue
        print(f"\n===== {item} {t['문장'][:70]}")
        number = (r.get("결과", {}).get("추출") or {}).get("숫자") or ""
        try:
            ans = baseline(t["문장"], article_context(t))
            kind, why = verify(ans, number) if number else ("확인 불가", "기사 숫자를 몰라 검사 못 함")
        except RuntimeError as e:
            ans, kind, why = {}, "오류", str(e)[:120]
        mine, mine_why = ours(r)
        done[item] = {"문장": t["문장"], "숫자": number, "기준선답": ans, "기준선": kind, "기준선근거": why,
                      "우리": mine, "우리근거": mine_why}
        print(f"    기준선: {kind} — {why} / 우리: {mine} — {mine_why}")
        store.write_text(json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = ["| 문장 | 기준선(검색+AI) | 근거 | 원래 어디서? | 근거 |", "|---|---|---|---|---|"]
    for k, d in done.items():
        rows.append(f"| {k} {d['문장'][:50]}… | {d['기준선']} | {d['기준선근거'][:60]} "
                    f"({(d['기준선답'].get('원문주소') or '')[:50]}) | {d['우리']} | {d['우리근거'][:60]} |")
    (OUT / "비교표.md").write_text("# 기준선 비교 (검색+AI 답변 vs 원래 어디서?)\n\n" + "\n".join(rows) + "\n", encoding="utf-8")
    print(f"\n비교표: {OUT / '비교표.md'}")


if __name__ == "__main__":
    main()
