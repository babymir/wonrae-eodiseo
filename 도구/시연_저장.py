"""시연용 저장 결과 만들기(11/16): 실제로 돌려 저장한 평가 결과에서 시연 문장의 결과와 진행 기록을 뽑는다.

라이너 크레딧이 다 떨어져 새로 돌릴 수 없어서, 영상·결선 시연은 저장한 실제 결과를 화면에 다시 보여 준다.
결과는 손대지 않고 그대로 옮긴다. 진행 기록은 같은 실행의 로그(실행_로그_<같은 꼬리>.txt)에서 그 문장 칸을 가져온다.
사용: python3 도구/시연_저장.py   → 작업/시연/저장결과.json
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "작업" / "시연" / "저장결과.json"
# (화면 버튼 이름, 결과 파일, 문장에 든 글자, 기사 날짜)
DEMOS = [
    ("보육 이용률 10.5%p(기사가 계산)", "작업/평가_새기사/평가결과_회귀2.json", "이주배경 가구가 10.5%p", "2026-11-11"),
    ("독감 22.8명(일치)", "작업/흔들림시험_B_무작위3/평가결과.json", "22.8명", "2026-11-14"),
    ("걸프 석유 1840만 배럴(표현 바뀜)", "작업/평가_무작위/평가결과_흔들림수정.json", "1840만배럴", "2026-11-14"),
    ("의원실 제출 자료(원문 없음 안내)", "작업/평가_인용장면/평가결과.json", "자영업 다중채무자", "2026-11-14"),
]
BLOCK = re.compile(r"^===== \d+/\d+ ", re.M)


def find_result(path: Path, key: str) -> dict:
    for row in json.loads(path.read_text(encoding="utf-8")):
        res = row.get("결과", row)
        if key in res.get("입력", ""):
            return res
    raise SystemExit(f"오류: {path} 에서 '{key}' 문장을 찾지 못했습니다.")


def find_steps(result_path: Path, sentence: str) -> list[str]:
    """같은 실행의 로그에서 그 문장 칸의 진행 줄. 로그가 없으면 []."""
    tail = result_path.stem.removeprefix("평가결과")
    log = result_path.with_name(f"실행_로그{tail}.txt")
    if not log.exists():
        print(f"  알림: 진행 기록 로그가 없어 단계 기록 없이 저장합니다({log.name})")
        return []
    text = log.read_text(encoding="utf-8", errors="replace")
    starts = [m.start() for m in BLOCK.finditer(text)] + [len(text)]
    for a, b in zip(starts, starts[1:]):
        block = text[a:b]
        if sentence[:40] in block.split("\n", 1)[0]:
            return [ln.rstrip() for ln in block.split("\n")[1:] if ln.strip() and not ln.startswith(("→", "판정:"))][:60]
    print(f"  알림: {log.name} 에서 이 문장의 진행 기록을 찾지 못했습니다")
    return []


def main() -> int:
    out = []
    for name, file, key, date in DEMOS:
        path = ROOT / file
        res = find_result(path, key)
        steps = find_steps(path, res["입력"])
        out.append({"이름": name, "문장": res["입력"], "날짜": date, "출처파일": file,
                    "실행시각": res.get("시각", ""), "진행": steps, "결과": res})
        print(f"{name}: {res.get('판정')} · 진행 {len(steps)}줄 · 실제 {(res.get('시간') or {}).get('총초')}초")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {OUT.relative_to(ROOT)} ({len(out)}건)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
