"""영상 합치기(11/16): 장면별 화면(녹화·그림) + 내레이션 + 자막 → 제출용 mp4 한 개.

먼저: 도구/영상/음성_만들기.py(음성), 도구/영상/녹화.py(시연 녹화), 도구/영상/그림.py(그림)
실행: python3 도구/영상/만들기.py   → 작업/영상/원래어디서_시연영상.mp4
장면 길이 = 그 장면 내레이션 길이(줄 사이 쉼 포함). 자막은 줄마다 그 음성이 나오는 동안 보인다.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
WORK = ROOT / "작업" / "영상"
VOICE, REC, PIC, TMP = WORK / "음성", WORK / "녹화", WORK / "그림", WORK / "조각"
OUT = WORK / "원래어디서_시연영상.mp4"
LEAD, GAP, TAIL = 0.4, 0.35, 0.6       # 장면 앞 쉼, 줄 사이 쉼, 장면 끝 쉼(초)
FPS, W, H = 30, 1920, 1080
BG = "0xf2f3f5"
FLIP_SHARE = 3                          # 문제 장면: 앞 3줄 동안 쪽 넘기기, 나머지는 AI 검색 그림
MAX_SEC = 300                           # 대회 제한 5분
FADE = 0.45                             # 장면이 바뀔 때 바탕색으로 흐려졌다 나타나는 시간(초)
REC_SKIP = 0.5                          # 녹화 앞의 빈 화면(페이지 여는 중) 건너뛰기
END_HOLD = 2.5                          # 마지막 장면은 사라지지 않고 이만큼 더 머문 뒤 끝남


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"오류: ffmpeg 실패\n{' '.join(cmd[:6])} …\n{r.stderr[-1500:]}")


def still(png: Path, sec: float, out: Path) -> None:
    run(["ffmpeg", "-y", "-loop", "1", "-t", f"{sec:.3f}", "-i", str(png), "-r", str(FPS),
         "-vf", f"scale={W}:{H},format=yuv420p", "-an", str(out)])


def fade(clip: Path, sec: float, out: bool = True) -> None:
    """장면 앞뒤를 바탕색에서 부드럽게 나타나고 사라지게(11/16: 장면이 '갑자기 바뀌는 느낌').
    out=False 면 나타나기만(마지막 장면은 결과 화면을 남긴 채 끝냄)."""
    tmp = clip.with_name(clip.stem + "_f.mp4")
    vf = f"fade=t=in:st=0:d={FADE}:c={BG}"
    if out:
        vf += f",fade=t=out:st={max(0, sec - FADE):.3f}:d={FADE}:c={BG}"
    run(["ffmpeg", "-y", "-i", str(clip), "-vf", vf + ",format=yuv420p", "-an", str(tmp)])
    tmp.replace(clip)


def fit_video(src: Path, sec: float, out: Path) -> None:
    """녹화를 sec 초로: 길면 자르고 짧으면 마지막 화면을 멈춰 늘린다."""
    run(["ffmpeg", "-y", "-ss", str(REC_SKIP), "-i", str(src), "-r", str(FPS),
         "-vf", f"scale={W}:{H},tpad=stop_mode=clone:stop_duration={sec:.3f},format=yuv420p",
         "-t", f"{sec:.3f}", "-an", str(out)])


def flip_video(sec: float, out: Path) -> None:
    frames = sorted((PIC / "쪽넘기기").glob("*.png"))
    if not frames:
        sys.exit("오류: 쪽 넘기기 그림이 없습니다. python3 도구/영상/그림.py 를 먼저 실행하세요.")
    rate = len(frames) / (sec * 0.8)    # 80% 동안 넘기고 마지막 쪽(재료 숫자 쪽)에서 멈춤
    run(["ffmpeg", "-y", "-framerate", f"{rate:.4f}", "-i", str(PIC / "쪽넘기기" / "%03d.png"), "-r", str(FPS),
         "-vf", f"scale=-2:{H - 80},pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={BG},"
                f"tpad=stop_mode=clone:stop_duration={sec:.3f},format=yuv420p",
         "-t", f"{sec:.3f}", "-an", str(out)])


def ass_time(t: float) -> str:
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def main() -> int:
    scenes = json.loads((HERE / "대사.json").read_text(encoding="utf-8"))
    lengths = json.loads((VOICE / "길이.json").read_text(encoding="utf-8"))
    TMP.mkdir(parents=True, exist_ok=True)
    clips, audio_parts, subs, clock = [], [], [], 0.0
    for s in scenes:
        name, durs = s["장면"], lengths.get(s["장면"], [])
        starts, t = [], LEAD
        for d in durs:
            starts.append(t)
            t += d + GAP
        sec = max(t - GAP + TAIL if durs else 0, s.get("최소초", 0))
        last = s is scenes[-1]
        if last:
            sec += END_HOLD
        clip = TMP / f"{name}.mp4"
        if s["종류"] == "녹화":
            src = REC / f"{name}.webm"
            if not src.exists():
                sys.exit(f"오류: 녹화가 없습니다: {src.relative_to(ROOT)} — python3 도구/영상/녹화.py 를 먼저 실행하세요.")
            fit_video(src, sec, clip)
        elif s["종류"] == "문제":
            split = starts[FLIP_SHARE] - 0.1
            a, b = TMP / f"{name}_a.mp4", TMP / f"{name}_b.mp4"
            flip_video(split, a)
            still(PIC / "문제_AI.png", sec - split, b)
            fade(a, split)
            fade(b, sec - split)
            lst = TMP / f"{name}.txt"
            lst.write_text(f"file '{a.name}'\nfile '{b.name}'\n", encoding="utf-8")
            run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(clip)])
        else:
            still(PIC / f"{s['그림']}.png", sec, clip)
        if s["종류"] != "문제":   # 문제 장면은 두 조각에 따로 넣었음
            fade(clip, sec, out=not last)
        clips.append(clip)
        for i, (st, d) in enumerate(zip(starts, durs)):
            audio_parts.append((clock + st, VOICE / f"{name}_{i}.mp3"))
            subs.append((clock + st, clock + st + d + 0.2, s["자막"][i]))
        print(f"{name}: {sec:.1f}초")
        clock += sec
    if clock > MAX_SEC:
        print(f"경고: 전체 {clock:.0f}초 — 대회 제한 {MAX_SEC}초를 넘습니다.")

    lst = TMP / "전체.txt"
    lst.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    video = TMP / "화면.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(video)])

    # 음성: 줄마다 제 시각에 놓고 섞는다
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-t", f"{clock:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    for _, mp3 in audio_parts:
        cmd += ["-i", str(mp3)]
    delays = "".join(f"[{i + 1}:a]adelay={int(st * 1000)}:all=1[a{i}];" for i, (st, _) in enumerate(audio_parts))
    mix = "[0:a]" + "".join(f"[a{i}]" for i in range(len(audio_parts)))
    voice = TMP / "음성.m4a"
    run(cmd + ["-filter_complex", f"{delays}{mix}amix=inputs={len(audio_parts) + 1}:normalize=0[out]",
               "-map", "[out]", "-c:a", "aac", "-b:a", "192k", str(voice)])

    ass = TMP / "자막.ass"
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, "
            "Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
            "Style: D,Pretendard SemiBold,46,&H00FFFFFF,&H00000000,&H99000000,0,3,14,0,2,120,120,56\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Text\n")
    ass.write_text(head + "".join(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},D,{t}\n" for a, b, t in subs if t),
                   encoding="utf-8")
    run(["ffmpeg", "-y", "-i", str(video), "-i", str(voice), "-vf", f"ass={ass}", "-c:v", "libx264", "-preset", "medium",
         "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", "-movflags", "+faststart", str(OUT)])
    print(f"완성: {OUT.relative_to(ROOT)} ({clock:.0f}초 = {int(clock // 60)}분 {int(clock % 60)}초)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
