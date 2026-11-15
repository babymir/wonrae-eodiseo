"""영상 내레이션 음성 만들기(11/16): 대사.json 의 '읽기' 줄마다 mp3 한 개.

음성: Microsoft 신경망 음성(edge-tts) ko-KR-HyunsuMultilingualNeural, 속도 +6% (PwC 시연 영상과 같은 설정).
읽는 글(대본)만 Microsoft 음성 서비스로 보낸다. 인터넷이 필요하다.
실행: <edge-tts가 설치된 python> 도구/영상/음성_만들기.py
     (설치: python3 -m venv <폴더> && <폴더>/bin/pip install edge-tts)
결과: 작업/영상/음성/<장면>_<번호>.mp3, 작업/영상/음성/길이.json(줄마다 초)
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import edge_tts

ROOT = Path(__file__).resolve().parents[2]
LINES = Path(__file__).resolve().parent / "대사.json"
OUT = ROOT / "작업" / "영상" / "음성"
VOICE = "ko-KR-HyunsuMultilingualNeural"
RATE = "+6%"
TRIES = 4


def duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


async def speak(text: str, path: Path) -> None:
    for attempt in range(1, TRIES + 1):
        try:
            await edge_tts.Communicate(text, VOICE, rate=RATE).save(str(path))
            return
        except edge_tts.exceptions.NoAudioReceived:
            print(f"  음성 받기 실패, 다시 시도({attempt}/{TRIES}): {path.name}")
            await asyncio.sleep(3 * attempt)
    sys.exit(f"오류: {path.name} 음성을 만들지 못했습니다. 인터넷 연결을 확인하고 다시 실행하세요.")


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    scenes = json.loads(LINES.read_text(encoding="utf-8"))
    info = {}
    for s in scenes:
        if len(s["읽기"]) != len(s["자막"]):
            sys.exit(f"오류: {s['장면']} 의 읽기 줄 수({len(s['읽기'])})와 자막 줄 수({len(s['자막'])})가 다릅니다.")
        secs = []
        for i, text in enumerate(s["읽기"]):
            path = OUT / f"{s['장면']}_{i}.mp3"
            await speak(text, path)
            secs.append(round(duration(path), 2))
        info[s["장면"]] = secs
        print(f"{s['장면']}: {len(secs)}줄 {sum(secs):.1f}초")
    (OUT / "길이.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"합계 {sum(sum(v) for v in info.values()):.1f}초 → {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
