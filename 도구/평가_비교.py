"""이전 차수 평가(사람 확인 칸 포함)와 이번 평가 결과를 건별로 나란히 놓는다.

사용: python3 도구/평가_비교.py <평가 폴더> <이전 평가표.md> [이전 평가결과.json]
예)   python3 도구/평가_비교.py 작업/평가 작업/평가/평가표_5차.md 작업/평가/평가결과_5차.json
출력: 건마다 [번호] 이전 판정·쪽·사람 확인 → 이번 판정·쪽·초, 바뀐 건에 ★
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def human_marks(table: Path) -> dict[int, str]:
    """평가표.md 의 마지막 칸(사람 확인)."""
    out = {}
    for line in table.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*(\d+)\s*\|", line)
        if m:
            out[int(m.group(1))] = line.rstrip("|").split("|")[-1].strip()
    return out


def brief(row: dict) -> str:
    r = row.get("결과", {})
    if not r:
        return f"오류({row.get('오류', '')[:40]})"
    page = f" p{r['쪽']}" if r.get("쪽") else ""
    calc = r.get("계산확인", {})
    if r.get("판정") == "기사가 계산" and calc.get("쪽"):
        page = f" p{calc['쪽']} {calc.get('식', '')}"
    return f"{r.get('판정')} {str(r.get('보고서', ''))[:28]}{page}"


def main() -> None:
    folder = ROOT / sys.argv[1]
    marks = human_marks(ROOT / sys.argv[2]) if len(sys.argv) > 2 else {}
    old = {r["번호"]: r for r in json.loads((ROOT / sys.argv[3]).read_text(encoding="utf-8"))} if len(sys.argv) > 3 else {}
    new = {r["번호"]: r for r in json.loads((folder / "평가결과.json").read_text(encoding="utf-8"))}
    secs_old = [r.get("초", 0) for r in old.values() if r.get("초")]
    secs_new = [r.get("초", 0) for r in new.values() if r.get("초")]
    for n in sorted(new):
        before = brief(old[n]) if n in old else "-"
        after = brief(new[n])
        star = "★" if before != after else " "
        print(f"{star}[{n}] {new[n]['문장'][:60]}")
        print(f"     이전: {before} ({old.get(n, {}).get('초', '-')}초) | 사람: {marks.get(n, '')[:70]}")
        print(f"     이번: {after} ({new[n].get('초')}초)")
    if secs_old:
        print(f"\n평균 시간: 이전 {sum(secs_old) / len(secs_old):.1f}초 → 이번 {sum(secs_new) / len(secs_new):.1f}초")
    else:
        print(f"\n평균 시간: 이번 {sum(secs_new) / len(secs_new):.1f}초")


if __name__ == "__main__":
    main()
