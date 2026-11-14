"""라이너 Deep Research vs 「원래 어디서?」 — 같은 문장, 같은 정답표, 같은 잣대(11/15, 인용 장면 세트).

질문 "라이너 Deep Research 로 되지 않나요?"에 답하기 위한 비교.
이전 도구(딥리서치_비교.py)는 '참고 주소에 정답 기관이 있나'만 봤다. 이 도구는 한 걸음 더 간다:
  Deep Research 가 '원문'이라고 지목한 주소를 실제로 열어, 인용한 문장과 기사 숫자가 그 문서에 정말 있는지 대조한다.

단계
  1) 묻기  : 세트의 모든 문장을 Deep Research 에 묻고 답을 그대로 저장(<세트>/딥리서치대조.json, 한 건마다 저장 → 이어서)
  2) 채점  : 정답표(정답찾기/결과_*.json)에서 공개 원문이 있는 문장만, 두 도구를 같은 칸으로 나눈다
     - 원문 문장 확인 : 지목한 주소가 정답 원문 기관 주소이고, 그 문서에 인용 문장과 기사 숫자가 실제로 있음
     - 다른 문서에서 확인: 인용은 그 문서에 있으나 정답 원문 기관이 아님(기사·블로그 등 옮긴 곳)
     - 지어낸 인용   : 지목한 주소 문서에서 인용 문장을 찾지 못함 (Deep Research 만 해당)
     - 링크만/못 찾음 : 주소나 인용을 내지 않음, 열 수 없음
     우리 도구는 정답대조.grade 로 같은 칸에 맞춘다.
     정답에 공개 원문이 없는 문장(비공개·대상 아님)에서 '원문을 찾았다'고 답한 건수도 센다(틀린 단정).

사용: python3 도구/딥리서치_대조.py 작업/평가_인용장면 묻기
      python3 도구/딥리서치_대조.py 작업/평가_인용장면 채점      (정답표·우리 결과가 다 있어야 함)
"""
import glob
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from liner_api import ask_json, json_schema
from 기준선_비교 import verify
from 라이너에이전트 import ask as dr_ask
from 정답대조 import HAS_ORIGINAL, QUOTE_FOUND, grade, site, wilson

WORKERS = 2            # Deep Research 동시 질문 수(사용량 제한을 넘지 않게 작게)
DR_TIMEOUT_SEC = 300   # 한 건 최대 대기
S = {"type": "string"}
PICK_SCHEMA = json_schema("dr_pick", {"원문주소": S, "원문문장": S})
PICK_PROMPT = """아래는 리서치 도우미의 답이다. 답이 '원 출처'로 지목한 문서 주소 하나와, 그 문서에서 인용했다고 한 문장을 그대로 뽑아라.
답에 없는 것은 지어내지 말고 빈 문자열로. JSON 하나로만.
답:
{answer}
참고 주소 목록:
{refs}"""
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:%|퍼센트|%포인트|조|억|만|천|명|건|원|달러|배|개|t|톤|GW)?")
_lock = threading.Lock()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def pick_claim(ans: dict) -> dict:
    """Deep Research 답에서 (원문주소, 원문문장). 끝 두 줄을 먼저 보고, 없으면 라이너 모델로 뽑는다."""
    text = ans.get("답", "")
    url = re.search(r"원문주소\s*[:：]\s*\**\s*<?(https?://[^\s>)\]]+)", text)
    quote = re.search(r"원문문장\s*[:：]\s*(.+)", text)
    if url and quote and "없음" not in quote.group(1)[:4]:
        return {"원문주소": url.group(1).rstrip(".,"), "원문문장": quote.group(1).strip(" *\"'“”")}
    refs = "\n".join(f"- {r.get('제목', '')} | {r.get('주소', '')}" for r in ans.get("참고", [])[:30])
    try:
        return ask_json(PICK_PROMPT.format(answer=text[-6000:], refs=refs), kind="DR답정리", effort="low",
                        schema=PICK_SCHEMA)
    except RuntimeError as e:
        print(f"    답 정리 실패(빈 값으로 둠): {e}")
        return {"원문주소": "", "원문문장": ""}


def ask_one(n: int, sentence: str, store: Path, done: dict) -> None:
    print(f"#{n} Deep Research 질문 중… {sentence[:45]}", flush=True)
    try:   # 질문은 라이너에이전트.QUESTION(원 출처 주소 + 그 문서 속 문장 그대로 인용)과 같다
        out = dr_ask(sentence, timeout=DR_TIMEOUT_SEC)
    except Exception as e:  # 한 건 실패는 이유를 남기고 다음 건으로
        out = {"오류": f"{type(e).__name__}: {str(e)[:200]}"}
    if "오류" not in out:
        out["지목"] = pick_claim(out)
    with _lock:
        done[str(n)] = out
        store.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"#{n} 끝 — {out.get('오류') or (out['지목'].get('원문주소') or '주소 없음')[:80]}", flush=True)


def cmd_ask(folder: Path) -> None:
    targets = json.loads((folder / "평가대상.json").read_text(encoding="utf-8"))
    store = folder / "딥리서치대조.json"
    done = load(store)
    todo = [t for t in targets if str(t["번호"]) not in done or "오류" in done[str(t["번호"])]]
    print(f"Deep Research 에 물을 문장 {len(todo)}건 (이미 {len(targets) - len(todo)}건 끝)")
    with ThreadPoolExecutor(WORKERS) as ex:
        for t in todo:
            ex.submit(ask_one, t["번호"], t["문장"], store, done)


def article_number(sentence: str, ours: dict) -> str:
    n = ((ours.get("결과") or {}).get("추출") or {}).get("숫자") or ""
    if n:
        return n
    m = NUMBER.search(sentence)
    return m.group(0).strip() if m else ""


def grade_dr(gt: dict, claim: dict, number: str) -> tuple[str, str]:
    kind, why = verify(claim, number)
    same_site = site(claim.get("원문주소", "")) == site(gt.get("원문주소", "")) and site(gt.get("원문주소", ""))
    if kind == "원문 확인":
        return ("원문 문장 확인" if same_site else "다른 문서에서 확인"), why
    if kind in ("답 없음", "링크만", "확인 불가"):
        return ("링크만(정답 기관)" if same_site else "링크만/못 찾음"), why
    return kind, why   # 지어낸 인용 / 인용은 있으나 숫자 없음


def grade_ours(gt: dict, r: dict) -> str:
    g = grade(gt, r.get("결과", {}))
    return {"근거 확인 필요": "다른 문서에서 확인"}.get(g, g)


def cmd_grade(folder: Path) -> None:
    gt = {g["번호"]: g for f in sorted(glob.glob(str(folder / "정답찾기" / "결과_*.json")))
          for g in json.loads(Path(f).read_text(encoding="utf-8"))}
    targets = {t["번호"]: t for t in json.loads((folder / "평가대상.json").read_text(encoding="utf-8"))}
    ours = {r["번호"]: r for r in json.loads((folder / "평가결과.json").read_text(encoding="utf-8"))}
    dr = load(folder / "딥리서치대조.json")
    missing = [n for n in targets if n not in gt or n not in ours or str(n) not in dr]
    if missing:
        raise SystemExit(f"정답표·우리 결과·Deep Research 답이 모두 있어야 채점합니다. 빠진 번호: {missing}")
    rows, tally, wrong = [], {"우리": {}, "DR": {}}, {"우리": [], "DR": []}
    for n in sorted(targets):
        g, r, d = gt[n], ours[n], dr[str(n)]
        number = article_number(targets[n]["문장"], r)
        claim = d.get("지목") or {}
        if g["분류"] in HAS_ORIGINAL:
            dk, why = grade_dr(g, claim, number) if "오류" not in d else ("오류", d["오류"])
            ok = grade_ours(g, r)
            for who, k in (("우리", ok), ("DR", dk)):
                tally[who][k] = tally[who].get(k, 0) + 1
            rows.append(f"| {n} | {g['분류'].replace('원문있음_미확인숫자', '위치만')} | {site(g['원문주소'])} | {ok} | {dk} | "
                        f"{site(claim.get('원문주소', '')) or '-'} | {why[:40]} |")
        else:   # 공개 원문 없는 문장에서 '찾았다'고 하면 틀린 단정
            if (r.get("결과") or {}).get("판정") in QUOTE_FOUND:
                wrong["우리"].append(n)
            if "오류" not in d and claim.get("원문주소") and verify(claim, number)[0] != "원문 확인":
                wrong["DR"].append(n)   # 원문이 없는데 주소+인용을 내고, 그 인용도 문서에 없음
    total = len(rows)
    lines = [f"# Deep Research vs 원래 어디서? — {folder.name}", "",
             f"공개 원문이 있는 문장 {total}건 (30건 중). 정답 원문은 에이전트가 직접 찾아 숫자를 본 것.", ""]
    for who, name in (("우리", "원래 어디서?"), ("DR", "라이너 Deep Research")):
        k = tally[who].get("원문 문장 확인", 0)
        lo, hi = wilson(k, total)
        lines.append(f"- **{name}**: 원문 문장 확인 {k}/{total} ({k / max(total, 1):.0%}, 95% 구간 {lo:.0%}~{hi:.0%}) · "
                     + " · ".join(f"{a} {b}" for a, b in sorted(tally[who].items(), key=lambda x: -x[1])))
    lines += ["", f"- 공개 원문이 없는 문장에서 원문을 찾았다고 한 건(틀린 단정): 우리 {wrong['우리'] or '0건'} · "
              f"Deep Research(문서에 없는 인용까지 냄) {wrong['DR'] or '0건'}", "",
              "| # | 정답 종류 | 정답 기관 | 원래 어디서? | Deep Research | DR이 지목한 기관 | DR 대조 근거 |",
              "|---|---|---|---|---|---|---|"] + rows
    out = folder / "딥리서치대조_표.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:8]))
    print(f"\n표: {out}")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[2] not in ("묻기", "채점"):
        raise SystemExit("사용: python3 도구/딥리서치_대조.py <세트 폴더> 묻기|채점")
    (cmd_ask if sys.argv[2] == "묻기" else cmd_grade)(Path(sys.argv[1]))
