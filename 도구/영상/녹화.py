"""시연 장면 녹화(11/16): 시연 재생 모드 웹서버 화면을 자동으로 조작하며 동영상으로 저장한다.

먼저 웹서버를 재생 모드로 켠다: ORIGIN_REPLAY=1 ORIGIN_PORT=8766 python3 도구/웹서버.py
실행: python3 도구/영상/녹화.py   → 작업/영상/녹화/<장면>.webm
- 문장을 한 글자씩 입력 → '원문 찾기' → 진행 과정 → 결과를 천천히 아래로 훑는다.
- 장면 길이는 그 장면 내레이션 길이(작업/영상/음성/길이.json)에 맞춘다.
- 기사 속 사람 이름(의원 이름)은 화면에서만 ○○○ 로 가린다(서버에는 원래 문장을 보내 저장 결과와 맞춤).
"""
import json
import shutil
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8766"
OUT = ROOT / "작업" / "영상" / "녹화"
SAVED = ROOT / "작업" / "시연" / "저장결과.json"
SIZE = {"width": 1920, "height": 1080}
ZOOM = 1.45                       # 화면 글자를 영상에서 읽히게 키움
TYPE_DELAY_MS = 28                # 한 글자 입력 간격
MASK = {"박성훈": "○○○"}          # 화면에서만 가릴 이름
EXTRA_SEC = 1.0                   # 내레이션 끝난 뒤 여유

INIT_JS = """
(() => {
  const MASK = %s;
  const fix = (s) => Object.entries(MASK).reduce((a, [k, v]) => a.split(k).join(v), s);
  const walk = (n) => {
    if (n.nodeType === 3) { const t = fix(n.nodeValue); if (t !== n.nodeValue) n.nodeValue = t; }
    else n.childNodes && n.childNodes.forEach(walk);
  };
  document.addEventListener('DOMContentLoaded', () => {
    document.documentElement.style.zoom = '%s';
    document.querySelectorAll('[data-s]').forEach((b) => b.dataset.s = fix(b.dataset.s));
    new MutationObserver((ms) => ms.forEach((m) => { m.addedNodes.forEach(walk); if (m.type === 'characterData') walk(m.target); }))
      .observe(document.body, { childList: true, subtree: true, characterData: true });
  });
})();
""" % (json.dumps(MASK, ensure_ascii=False), ZOOM)


def unmask(body: str) -> str:
    for real, shown in MASK.items():
        body = body.replace(shown, real)
    return body


def smooth_scroll(page, seconds: float) -> None:
    """결과 영역 위부터 대조표 끝까지 천천히 내린다."""
    total = page.evaluate("() => Math.max(0, document.documentElement.scrollHeight * %s - innerHeight)" % ZOOM)
    start = page.evaluate("() => scrollY")
    steps = max(1, int(seconds * 30))
    for i in range(1, steps + 1):
        y = start + (total - start) * (i / steps)
        page.evaluate(f"window.scrollTo(0, {y / ZOOM})")
        time.sleep(seconds / steps)


def record(pw, name: str, sentence: str, target: float) -> Path:
    browser = pw.chromium.launch()
    tmp = OUT / f"_{name}"
    ctx = browser.new_context(viewport=SIZE, record_video_dir=str(tmp), record_video_size=SIZE)
    ctx.add_init_script(INIT_JS)
    ctx.route("**/api/check", lambda route: route.continue_(post_data=unmask(route.request.post_data or "")))
    page = ctx.new_page()
    began = time.time()
    page.goto(URL)
    time.sleep(0.8)
    shown = sentence
    for real, mask in MASK.items():
        shown = shown.replace(real, mask)
    page.click("#sentence")
    page.keyboard.type(shown, delay=TYPE_DELAY_MS)
    time.sleep(0.5)
    page.click("#go")
    page.wait_for_selector("#result:not([hidden])", timeout=30000)
    time.sleep(1.2)
    page.evaluate("document.querySelector('#result').scrollIntoView({block: 'start'})")
    left = target - (time.time() - began)
    hold = 1.5
    smooth_scroll(page, max(2.0, left - hold))
    time.sleep(hold)
    video = page.video
    ctx.close()
    browser.close()
    out = OUT / f"{name}.webm"
    shutil.move(video.path(), out)
    shutil.rmtree(tmp, ignore_errors=True)
    return out


def main() -> int:
    lines = json.loads((HERE / "대사.json").read_text(encoding="utf-8"))
    lengths = json.loads((ROOT / "작업" / "영상" / "음성" / "길이.json").read_text(encoding="utf-8"))
    saved = json.loads(SAVED.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    only = sys.argv[1:]
    with sync_playwright() as pw:
        for s in lines:
            if s["종류"] != "녹화" or (only and s["장면"] not in only):
                continue
            target = sum(lengths[s["장면"]]) + 0.35 * len(lengths[s["장면"]]) + EXTRA_SEC
            out = record(pw, s["장면"], saved[s["시연"]]["문장"], target)
            print(f"{s['장면']}: {out.relative_to(ROOT)} (목표 {target:.1f}초)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
