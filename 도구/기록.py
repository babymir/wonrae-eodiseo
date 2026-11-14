"""한 건 처리의 단계별 걸린 시간·라이너 호출 기록·화면용 진행 이벤트.

- step("검색"): with 블록으로 감싸면 그 단계의 초를 모은다(같은 이름은 더함).
- api_call(...): liner_api.py 가 호출마다 남긴다(무엇을, 어느 모델이 답했는지, 몇 초, 토큰).
- emit(종류, 데이터): 웹 화면에 단계별 결과(후보 보고서·찾은 쪽·임시 판정)를 먼저 보낸다.
한 번에 한 건만 처리하므로(웹서버 RUN_LOCK, 평가 순차) 모듈 전역 상태 하나를 쓴다.
안에서 스레드를 여러 개 띄우므로 잠금(lock)으로 보호한다.
"""
import threading
import time
from contextlib import contextmanager

_lock = threading.Lock()
_state: dict = {"start": 0.0, "steps": {}, "calls": [], "listener": None}


def start(listener=None) -> None:
    """새 건 시작. listener(종류, 데이터): 진행 이벤트를 받을 함수(웹 화면용, 없으면 None)."""
    with _lock:
        _state.update(start=time.time(), steps={}, calls=[], listener=listener)


@contextmanager
def step(name: str):
    t = time.time()
    try:
        yield
    finally:
        with _lock:
            _state["steps"][name] = round(_state["steps"].get(name, 0) + time.time() - t, 2)


def api_call(kind: str, model: str, secs: float, usage: dict | None = None) -> None:
    usage = usage or {}
    details = usage.get("completion_tokens_details") or {}
    with _lock:
        _state["calls"].append({"무엇": kind, "모델": model, "초": round(secs, 2),
                                "입력토큰": usage.get("prompt_tokens"), "출력토큰": usage.get("completion_tokens"),
                                "추론토큰": details.get("reasoning_tokens")})


def emit(kind: str, data) -> None:
    listener = _state.get("listener")
    if listener:
        try:
            listener(kind, data)
        except Exception as e:  # 화면 전달 실패가 본 작업을 멈추게 하지 않음(이유는 출력)
            print(f"    (화면 진행 전달 실패: {e})")


def summary() -> dict:
    with _lock:
        return {"총초": round(time.time() - _state["start"], 1), "단계초": dict(_state["steps"]),
                "라이너호출": list(_state["calls"])}
