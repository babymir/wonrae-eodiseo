"""원문 찾기를 별도 프로그램으로 돌리고, 시간 상한이 지나면 강제로 끝낸다(11/16).

같은 프로그램 안의 시간 상한(원문찾기.TIME_LIMIT_SEC)은 큰 PDF·느린 누리집 처리 중에 제때 깨지 못하는 경우가
있었다(한 건 880초). 별도 프로그램은 운영체제가 끝낼 수 있어 어떤 경우에도 상한을 지킨다.
- 자식: python3 도구/격리실행.py --자식  (표준입력으로 JSON 인자, 표준출력으로 진행 줄·@@EVENT·@@RESULT)
- 부모: run_isolated(...) — 진행 줄은 on_line, 이벤트는 on_event 로 넘기고, 상한+여유가 지나면 끝냄
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
GRACE_SEC = 20          # 안쪽 상한(원문찾기)이 정리할 여유
EVENT, RESULT = "@@EVENT ", "@@RESULT "


def _child() -> None:
    args = json.loads(sys.stdin.read())
    sys.path.insert(0, str(HERE))
    from 원문찾기 import run

    def on_event(kind, data):
        print(EVENT + json.dumps({"종류": kind, "내용": data}, ensure_ascii=False, default=str), flush=True)

    result = run(args["sentence"], args.get("context", ""), args.get("date", ""), force=args.get("force", False),
                 on_event=on_event, prefer=args.get("prefer"))
    print(RESULT + json.dumps(result, ensure_ascii=False, default=str), flush=True)


def run_isolated(sentence: str, context: str = "", date: str = "", force: bool = False, prefer: dict | None = None,
                 on_line=None, on_event=None, limit: float | None = None) -> dict:
    """별도 프로그램으로 원문 찾기. limit(초, 기본 ORIGIN_TIME_LIMIT 또는 150) + GRACE_SEC 가 지나면 강제 종료."""
    limit = limit or float(os.environ.get("ORIGIN_TIME_LIMIT", "150"))
    proc = subprocess.Popen([sys.executable, "-u", str(Path(__file__)), "--자식"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(HERE.parent))
    proc.stdin.write(json.dumps({"sentence": sentence, "context": context, "date": date, "force": force,
                                 "prefer": prefer}, ensure_ascii=False))
    proc.stdin.close()
    box: dict = {"result": None, "candidates": []}

    def reader() -> None:
        for line in proc.stdout:
            line = line.rstrip("\n")
            if line.startswith(RESULT):
                box["result"] = json.loads(line[len(RESULT):])
            elif line.startswith(EVENT):
                ev = json.loads(line[len(EVENT):])
                if ev.get("종류") == "후보":
                    box["candidates"] = ev.get("내용") or []
                if on_event:
                    on_event(ev.get("종류"), ev.get("내용"))
            elif on_line and line.strip():
                on_line(line)

    t = threading.Thread(target=reader, daemon=True)
    start = time.time()
    t.start()
    try:
        proc.wait(timeout=limit + GRACE_SEC)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        if on_line:
            on_line(f"    시간 상한 {limit:.0f}초(+{GRACE_SEC}초)를 넘겨 원문 찾기를 끝냄")
    t.join(timeout=5)
    if box["result"] is not None:
        return box["result"]
    return {"입력": sentence, "판정": "원출처 미확인", "시간초과": True,
            "후보": [{"제목": str(x), "주소": ""} for x in box["candidates"][:5]],
            "이유": f"{limit:.0f}초 안에 원문 대조를 마치지 못했습니다(누리집 응답이 느리거나 문서가 큼). "
                   "잠시 뒤 다시 확인하면 저장된 자료로 더 빨리 끝납니다.",
            "시간": {"총초": round(time.time() - start, 1), "단계초": {}, "라이너호출": []}}


if __name__ == "__main__":
    if "--자식" in sys.argv:
        _child()
