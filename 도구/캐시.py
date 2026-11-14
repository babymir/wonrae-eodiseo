"""디스크 캐시: 같은 일을 다시 하지 않게 결과를 작업/캐시/ 에 JSON 으로 저장한다.

- 라이너 검색 결과: (검색어, 기간 조건) 기준, CACHE_HOURS 시간 동안 다시 씀(라이너 검색 문서 권장)
- PDF 쪽 글자: PDF 파일 이름·크기 기준(파일이 바뀌면 다시 뽑음)
- 모델 답: 기본은 끔. 환경변수 LINER_CACHE_MODEL=1 일 때만(시연용). 평가 때는 켜지 말 것.
캐시가 깨졌으면 지우고 새로 만든다(조용히 넘기지 않고 이유 출력).
"""
import hashlib
import json
import os
import time
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / "작업" / "캐시"
SEARCH_HOURS = 24          # 일반 검색 결과를 다시 쓸 시간
RECENT_SEARCH_HOURS = 3    # '최근 하루·한 주' 조건 검색은 짧게(막 나온 자료를 놓치지 않게)


def _path(kind: str, key: str) -> Path:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return CACHE_DIR / kind / f"{digest}.json"


def get(kind: str, key: str, max_hours: float | None = None):
    path = _path(kind, key)
    if not path.exists():
        return None
    if max_hours is not None and time.time() - path.stat().st_mtime > max_hours * 3600:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"    (캐시 파일이 깨져 다시 만듦: {path.name} — {e})")
        path.unlink(missing_ok=True)
        return None


def put(kind: str, key: str, value) -> None:
    path = _path(kind, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # 쓰다 끊겨도 반쪽 파일이 남지 않게


def model_cache_on() -> bool:
    return os.environ.get("LINER_CACHE_MODEL") == "1"


def file_key(path: Path) -> str:
    st = path.stat()
    return f"{path.name}|{st.st_size}|{int(st.st_mtime)}"
