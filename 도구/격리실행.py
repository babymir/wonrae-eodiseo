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
TAIL_LINES = 30         # 결과 없이 끝났을 때 원인을 찾으려고 남기는 마지막 출력 줄 수


def _crash_reason(tail: list[str]) -> str:
    """자식이 결과 없이 끝났을 때 사용자에게 보일 이유(11/16: 라이너 크레딧 부족이 '시간 초과'로 보였음)."""
    text = "\n".join(tail)
    if "HTTP 402" in text or "Insufficient credits" in text:
        return "라이너 API 크레딧이 부족해 원문을 찾지 못했습니다(HTTP 402). 크레딧을 충전한 뒤 다시 시도해 주세요."
    if "HTTP 401" in text or "HTTP 403" in text:
        return "라이너 API 키가 맞지 않거나 권한이 없어 원문을 찾지 못했습니다(.env 의 키를 확인해 주세요)."
    last = next((t.strip() for t in reversed(tail) if t.strip()), "")
    return f"원문 찾기 중 오류로 멈췄습니다: {last[:200]}" if last else "원문 찾기 프로그램이 결과 없이 끝났습니다."


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
    box: dict = {"result": None, "candidates": [], "tail": []}
    done = threading.Event()   # 결과 줄이 오면 바로 알림

    def reader() -> None:
        for line in proc.stdout:
            line = line.rstrip("\n")
            if line.startswith(RESULT):
                box["result"] = json.loads(line[len(RESULT):])
                done.set()
            elif line.startswith(EVENT):
                ev = json.loads(line[len(EVENT):])
                if ev.get("종류") == "후보":
                    box["candidates"] = ev.get("내용") or []
                if on_event:
                    on_event(ev.get("종류"), ev.get("내용"))
            else:
                box["tail"] = (box["tail"] + [line])[-TAIL_LINES:]
                if on_line and line.strip() and not done.is_set():
                    on_line(line)
        done.set()   # 결과 없이 끝남(오류 등)

    def reaper() -> None:
        """결과를 낸 뒤에도 자식은 미리 보낸 Deep Research 답을 마저 받아 저장하고 끝난다(11/16: 이걸 기다려
        답이 35초에 나왔는데 105초로 보였음). 상한+여유가 지나면 끝낸다."""
        try:
            proc.wait(timeout=max(1, limit + GRACE_SEC - (time.time() - start)))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    start = time.time()
    threading.Thread(target=reader, daemon=True).start()
    finished = done.wait(timeout=limit + GRACE_SEC)
    if not finished:
        proc.kill()
        proc.wait()
        if on_line:
            on_line(f"    시간 상한 {limit:.0f}초(+{GRACE_SEC}초)를 넘겨 원문 찾기를 끝냄")
    else:
        threading.Thread(target=reaper, daemon=True).start()
    if box["result"] is not None:
        return box["result"]
    if finished:   # 시간 안에 끝났는데 결과가 없음 = 자식이 오류로 멈춤
        reason = _crash_reason(box["tail"])
        if on_line:
            on_line("    " + reason)
        return {"입력": sentence, "판정": "원출처 미확인", "오류": reason, "이유": reason, "후보": [],
                "시간": {"총초": round(time.time() - start, 1), "단계초": {}, "라이너호출": []}}
    return {"입력": sentence, "판정": "원출처 미확인", "시간초과": True,
            "후보": [{"제목": str(x), "주소": ""} for x in box["candidates"][:5]],
            "이유": f"{limit:.0f}초 안에 원문 대조를 마치지 못했습니다(누리집 응답이 느리거나 문서가 큼). "
                   "잠시 뒤 다시 확인하면 저장된 자료로 더 빨리 끝납니다.",
            "시간": {"총초": round(time.time() - start, 1), "단계초": {}, "라이너호출": []}}


if __name__ == "__main__":
    if "--자식" in sys.argv:
        _child()
