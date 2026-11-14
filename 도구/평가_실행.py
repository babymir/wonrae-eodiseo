"""평가대상.json 의 기사 문장을 하나씩 에이전트에 넣고 결과·걸린 시간을 모은다.

사용: python3 도구/평가_실행.py [평가 폴더] [--건너뛰기=23,25] [--문장만]
      --문장만: 기사 문맥·날짜 없이 문장만 넣는다(실제 사용자가 문장 하나만 붙여 넣는 상황, A6).
                결과는 평가결과_문장만.json / 평가표_문장만.md 에 따로 저장
입력: 작업/평가/평가대상.json  (평가_기사모으기.py 결과)
출력: 작업/평가/평가결과.json (한 건 끝날 때마다 저장 → 다시 실행하면 이어서)
      작업/평가/평가표.md     (사람이 맞는지 확인할 표)
"""
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

from 기사받기 import source_clues
from 일반경로 import html_text
from 원문찾기 import run
from 격리실행 import run_isolated

ROOT = Path(__file__).resolve().parent.parent
# 평가 폴더: 기본은 작업/평가, 다른 세트는 인자로 (예: python3 도구/평가_실행.py 작업/평가_새기사)
_ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
EVAL_DIR = ROOT / _ARGS[0] if _ARGS else ROOT / "작업" / "평가"
# --건너뛰기=23,25 : 그 번호는 이번 실행에서 돌리지 않음(누리집이 멈추는 건 등). 결과에 남기지 않음
SKIP = {n.strip() for a in sys.argv[1:] if a.startswith("--건너뛰기=") for n in a.split("=", 1)[1].split(",")}
CACHE_DIR = EVAL_DIR / "기사캐시"
CONTEXT_CHARS = 600
SENTENCE_ONLY = "--문장만" in sys.argv
ISOLATE = os.environ.get("ORIGIN_ISOLATE") == "1"
_SUFFIX = "_문장만" if SENTENCE_ONLY else ""
TARGETS = EVAL_DIR / "평가대상.json"
RESULTS = EVAL_DIR / f"평가결과{_SUFFIX}.json"
TABLE = EVAL_DIR / f"평가표{_SUFFIX}.md"


def _clues_from_cache(t: dict) -> str:
    """기사캐시(.html)가 있으면 기사 전체에서 출처 단서 문장(화면의 '기사 주소' 입력과 같은 동작)."""
    page = CACHE_DIR / (hashlib.md5(t.get("기사주소", "").encode()).hexdigest() + ".html")
    if not page.exists():
        return ""
    flat = re.sub(r"\s+", " ", html_text(page.read_bytes()))
    return source_clues(flat, t["문장"])


def article_context(t: dict) -> str:
    """기사 제목 + 문장 앞뒤 CONTEXT_CHARS 글자(기사캐시에서). 사용자가 기사 링크를 같이 넣는 상황을 흉내 낸다."""
    if t.get("문맥"):  # 새 기사 세트는 모을 때 문맥을 같이 저장해 둠
        return t["문맥"] + _clues_from_cache(t)
    cache = CACHE_DIR / (hashlib.md5(t["기사주소"].encode()).hexdigest() + ".txt")
    if not cache.exists():
        return t.get("기사제목", "")
    text = re.sub(r"\s+", " ", cache.read_text(encoding="utf-8"))
    pos = text.find(re.sub(r"\s+", " ", t["문장"])[:20])
    if pos < 0:
        return t.get("기사제목", "")
    return f"[제목] {t.get('기사제목', '')} [본문] {text[max(0, pos - CONTEXT_CHARS): pos + CONTEXT_CHARS]}"


def load_results() -> dict:
    if RESULTS.exists():
        return {str(r["번호"]): r for r in json.loads(RESULTS.read_text(encoding="utf-8"))}
    return {}


def save_results(done: dict) -> None:
    rows = sorted(done.values(), key=lambda r: r["번호"])
    RESULTS.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def short(text: str, n: int) -> str:
    text = (text or "").replace("|", "/").replace("\n", " ")
    return text if len(text) <= n else text[:n] + "…"


def write_table(rows: list[dict]) -> None:
    lines = ["| # | 기관 | 기사 문장 | 판정 | 원 보고서·쪽 | 원문 인용 / 근거 | 초 | 사람 확인 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        res = r.get("결과", {})
        verdict = res.get("판정", "오류")
        where = f"{short(res.get('보고서', ''), 30)} p{res['쪽']}" if res.get("쪽") else short(res.get("보고서", ""), 30)
        if verdict in ("일치", "표현 바뀜"):
            basis = short(res.get("원문인용", ""), 90)
            diffs = "; ".join(f"{d.get('항목')}: {d.get('기사')}→{d.get('원문')}" for d in res.get("차이", []))
            if diffs:
                basis += f" 〔{short(diffs, 80)}〕"
            if res.get("확인못한부분"):
                basis += f" 〔확인 못 함: {short(res['확인못한부분'], 40)}〕"
            basis += f" ({res.get('찾은방법', '')})"
        elif verdict == "기사가 계산":
            basis = f"계산: {res.get('계산확인', {}).get('식')}"
        elif verdict == "통계표·원자료 인용":
            tables = res.get("통계표후보", [])
            basis = "통계표 후보: " + (short(tables[0]["제목"], 40) if tables else "없음")
        elif verdict == "원문에 없음":
            basis = "원 보고서에서 숫자를 못 찾고 계산도 안 됨" + (f" (기준시점 {res.get('기준시점확인')})")
        elif verdict == "오류":
            basis = short(r.get("오류", ""), 90)
        else:
            basis = short(res.get("이유", ""), 90)
        lines.append(f"| {r['번호']} | {r['기관']} | {short(r['문장'], 80)} | {verdict} | {where} | "
                     f"{basis} | {r.get('초', '')} | |")
    count = Counter(r.get("결과", {}).get("판정", "오류") for r in rows)
    secs = [r["초"] for r in rows if r.get("초")]
    summary = (f"\n총 {len(rows)}건 — " + ", ".join(f"{k} {v}" for k, v in count.most_common())
               + (f" / 평균 {sum(secs) / len(secs):.0f}초" if secs else ""))
    steps: dict[str, list[float]] = {}
    for r in rows:   # 단계별 평균 시간(11/13부터 결과에 '시간'이 기록됨)
        for k, v in (r.get("결과", {}).get("시간", {}).get("단계초") or {}).items():
            steps.setdefault(k, []).append(v)
    if steps:
        summary += "\n단계별 평균(초, 그 단계를 거친 건만): " + ", ".join(
            f"{k} {sum(v) / len(v):.1f}({len(v)}건)" for k, v in steps.items())
    TABLE.write_text("# 실제 기사 평가표\n\n" + "\n".join(lines) + "\n" + summary + "\n", encoding="utf-8")
    print(summary)


def main() -> None:
    targets = json.loads(TARGETS.read_text(encoding="utf-8"))
    done = load_results()
    for t in targets:
        key = str(t["번호"])
        if key in done and "결과" in done[key]:
            continue  # 이미 끝난 건은 건너뜀(오류였던 건만 다시)
        if key in SKIP:
            print(f"\n===== {key}번 건너뜀(--건너뛰기)")
            continue
        print(f"\n===== {t['번호']}/{len(targets)} [{t['기관']}] {t['문장'][:70]}")
        start = time.time()
        row = dict(t)
        try:
            # ORIGIN_ISOLATE=1: 별도 프로그램(시간 상한 보장, 화면과 같은 조건). 진행 줄은 로그에 그대로 찍는다
            runner = (lambda *a: run_isolated(*a, on_line=print)) if ISOLATE else run
            row["결과"] = (runner(t["문장"]) if SENTENCE_ONLY
                          else runner(t["문장"], article_context(t), t.get("기사날짜") or ""))
        except Exception as e:  # 한 건 실패해도 나머지는 계속, 이유는 기록
            row["오류"] = f"{type(e).__name__}: {e}"
            print(f"    실패: {row['오류']}")
        row["초"] = round(time.time() - start)
        done[key] = row
        save_results(done)
    write_table(sorted(done.values(), key=lambda r: r["번호"]))
    print(f"평가표: {TABLE}")


if __name__ == "__main__":
    main()
