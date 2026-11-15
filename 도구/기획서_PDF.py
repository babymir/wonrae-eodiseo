"""기획서 PDF 만들기(11/16): 제출/기획서.md → 제출/원래어디서_기획서.pdf (대회 제출: PDF, 10MB 이하).

1) 그림: 제출/그림/대표화면.png(시연 재생 화면), 흐름도.png, API.png(영상 그림과 같은 것)
2) pandoc 으로 HTML 로 바꾸고, 브라우저(Playwright)로 A4 PDF 를 찍는다.
먼저 웹서버를 재생 모드로 켠다: ORIGIN_REPLAY=1 ORIGIN_PORT=8766 python3 도구/웹서버.py
실행: python3 도구/기획서_PDF.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SUBMIT = ROOT / "제출"
MD = SUBMIT / "기획서.md"
PDF = SUBMIT / "원래어디서_기획서.pdf"
PIC = SUBMIT / "그림"
URL = "http://127.0.0.1:8766"
DEMO_BUTTON = 1                      # 대표 화면: '써 보기' 첫 버튼(보육 이용률 격차 — 기사가 계산)
MAX_MB = 10
API_PIC = ROOT / "작업" / "영상" / "그림" / "API.png"

FLOW_HTML = """<!doctype html><meta charset="utf-8"><style>
body { margin: 0; width: 1200px; background: #fff; font-family: 'Pretendard', 'Apple SD Gothic Neo', sans-serif; color: #1d2129; }
.row { display: flex; align-items: stretch; gap: 0; padding: 24px; }
.box { flex: 1; border: 1px solid #c9ced6; padding: 16px 14px; background: #f7f8fa; }
.box b { display: block; font-size: 28px; margin-bottom: 8px; }
.box span { font-size: 21px; color: #4b5260; line-height: 1.45; }
.arrow { width: 34px; display: flex; align-items: center; justify-content: center; font-size: 26px; color: #6b7280; }
.out { display: flex; gap: 0; padding: 0 24px 24px; }
.out div { flex: 1; border: 1px solid #c9ced6; padding: 14px; font-size: 20px; }
.out div + div { border-left: none; }
.out b { display: block; font-size: 23px; margin-bottom: 4px; }
</style><body>
<div class="row">
<div class="box"><b>1 입구</b><span>확인할 통계 문장인지<br>(Decisions)</span></div><div class="arrow">→</div>
<div class="box"><b>2 문장 분해</b><span>기관·보고서·숫자·핵심어·시점<br>(Model)</span></div><div class="arrow">→</div>
<div class="box"><b>3 후보 찾기</b><span>기관 누리집·원어·논문·공식 경로<br>(Search·Scholar)</span></div><div class="arrow">→</div>
<div class="box"><b>4 고르기</b><span>원문다운 후보 앞으로<br>(Rerank)</span></div>
</div>
<div class="row" style="padding-top:0">
<div class="box"><b>5 원문 읽기</b><span>PDF·HWP·첨부·공시<br>(Gateway)</span></div><div class="arrow">→</div>
<div class="box"><b>6 숫자 찾기</b><span>표기 바꿔 찾기<br>(코드)</span></div><div class="arrow">→</div>
<div class="box"><b>7 판정</b><span>칸별 비교·교차 확인<br>(Model·Decisions)</span></div><div class="arrow">→</div>
<div class="box"><b>8 헛답 막기</b><span>베낀 기사·다른 기관·옛 문서 거름<br>(코드)</span></div>
</div>
<div class="out">
<div><b>원문 확인됨</b>일치 · 표현 바뀜 · 기사가 계산 · 숫자 다름 — 원문 쪽·문장을 그대로</div>
<div><b>사람 확인 필요</b>표현 다름 후보 · 통계표 인용</div>
<div><b>원문 못 찾음</b>지어내지 않고 이유와 확인할 곳 안내 (못 찾으면 Deep Research 후보도 직접 확인)</div>
</div></body>"""

CSS = """
@page { size: A4; margin: 16mm 15mm 16mm 15mm; }
body { font-family: 'Pretendard', 'Apple SD Gothic Neo', sans-serif; font-size: 10.2pt; line-height: 1.6; color: #1d2129; }
h1 { font-size: 20pt; margin: 0 0 6pt; letter-spacing: -0.5pt; }
h2 { font-size: 14pt; margin: 18pt 0 6pt; padding-bottom: 3pt; border-bottom: 1px solid #c9ced6; break-after: avoid; }
h3 { font-size: 11.5pt; margin: 12pt 0 4pt; break-after: avoid; }
blockquote { margin: 0 0 8pt; padding: 6pt 10pt; background: #f2f3f5; color: #4b5260; border: 1px solid #dfe3e8; }
blockquote p { margin: 0; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 8pt; font-size: 9.2pt; break-inside: avoid; }
th, td { border: 1px solid #c9ced6; padding: 4pt 6pt; vertical-align: top; text-align: left; }
th { background: #e9ecf0; font-weight: 600; }
img { max-width: 100%; border: 1px solid #c9ced6; display: block; margin: 6pt 0; break-inside: avoid; }
hr { border: none; border-top: 1px solid #dfe3e8; margin: 12pt 0; }
code { font-size: 9pt; background: #f2f3f5; padding: 0 2pt; }
ul, ol { padding-left: 16pt; margin: 4pt 0; }
li { margin: 1pt 0; }
p { margin: 4pt 0; }
"""


def demo_screen(page) -> None:
    """시연 재생 화면에서 판정 표 + 대조표 윗부분을 잘라 찍는다."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(URL)
    page.click(f"button.sample:nth-of-type({DEMO_BUTTON})")
    page.click("#go")
    page.wait_for_selector("#result:not([hidden])", timeout=30000)
    page.wait_for_timeout(2500)   # 원문 쪽 그림이 뜰 때까지
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(300)
    box = page.locator("#result").bounding_box()
    page.screenshot(path=str(PIC / "대표화면.png"),
                    clip={"x": box["x"] - 10, "y": box["y"], "width": box["width"] + 20, "height": min(box["height"], 1250)},
                    full_page=True)


def main() -> int:
    PIC.mkdir(exist_ok=True)
    if not API_PIC.exists():
        sys.exit("오류: API 그림이 없습니다. python3 도구/영상/그림.py 를 먼저 실행하세요.")
    shutil.copy(API_PIC, PIC / "API.png")
    body = subprocess.run(["pandoc", str(MD), "-f", "gfm", "-t", "html"], capture_output=True, text=True)
    if body.returncode != 0:
        sys.exit(f"오류: pandoc 변환 실패: {body.stderr[:300]}")
    html = (f"<!doctype html><html lang='ko'><meta charset='utf-8'><base href='{SUBMIT.as_uri()}/'>"
            f"<style>{CSS}</style><body>{body.stdout}</body></html>")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        try:
            demo_screen(page)
        except Exception as e:  # 서버가 꺼져 있으면 이유를 알리고 멈춤
            sys.exit(f"오류: 대표 화면을 찍지 못했습니다({e}). 재생 모드 웹서버(8766)가 켜져 있는지 확인하세요.")
        page.set_viewport_size({"width": 1200, "height": 400})
        page.set_content(FLOW_HTML)
        page.locator("body").screenshot(path=str(PIC / "흐름도.png"))
        tmp = SUBMIT / "_기획서_임시.html"
        tmp.write_text(html, encoding="utf-8")
        page.goto(tmp.as_uri())
        page.wait_for_timeout(500)
        page.pdf(path=str(PDF), format="A4", print_background=True,
                 display_header_footer=True, header_template="<span></span>",
                 footer_template="<div style='font-size:8px;width:100%;text-align:center;color:#888'>"
                                 "<span class='pageNumber'></span> / <span class='totalPages'></span></div>",
                 margin={"top": "16mm", "bottom": "18mm", "left": "15mm", "right": "15mm"})
        tmp.unlink()
        browser.close()
    mb = PDF.stat().st_size / 1e6
    print(f"완성: {PDF.relative_to(ROOT)} ({mb:.1f}MB)")
    if mb > MAX_MB:
        print(f"경고: 대회 제한 {MAX_MB}MB를 넘습니다. 그림 크기를 줄여야 합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
