"""영상용 그림 만들기(11/16): 제목·성적표·API 지도·마무리 화면, 문제 장면(원문 PDF 쪽 넘기기) 그림.

화면과 같은 모양(흰 바탕, 실선 표, Pretendard)으로 HTML 을 그려 1920x1080 PNG 로 찍는다.
실행: python3 도구/영상/그림.py   → 작업/영상/그림/*.png, 작업/영상/그림/쪽넘기기/*.png
숫자는 제출/기획서.md 의 처음 보는 문장 첫 측정 값과 같아야 한다(바꾸면 둘 다 고칠 것).
"""
import base64
import sys
from pathlib import Path

import fitz  # PyMuPDF
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "작업" / "영상" / "그림"
FLIP_PDF = ROOT / "작업" / "원본PDF" / "kihasa_30342.pdf"   # 보육 장면의 원 보고서(178쪽에 재료 숫자)
FLIP_TARGET = 178
FLIP_FRAMES = 60
RESULT_PIC = ROOT / "제출" / "그림" / "대표화면.png"   # 마무리 화면 오른쪽: 실제 결과 화면(기획서와 같은 그림)

def _b64(path: Path) -> str:
    """그림을 HTML 안에 넣는다(빈 페이지에서는 파일 경로 그림을 브라우저가 막음)."""
    if not path.exists():
        sys.exit(f"오류: 그림이 없습니다: {path.relative_to(ROOT)} — python3 도구/기획서_PDF.py 를 먼저 실행하세요.")
    return base64.b64encode(path.read_bytes()).decode()


CSS = """
@font-face { font-family: P; src: local('Pretendard'); }
* { box-sizing: border-box; margin: 0; }
body { width: 1920px; height: 1080px; background: #f2f3f5; font-family: 'Pretendard', 'Apple SD Gothic Neo', sans-serif;
       color: #1d2129; display: flex; flex-direction: column; justify-content: center; padding: 0 160px; }
h1 { font-size: 88px; font-weight: 800; letter-spacing: -2px; }
h2 { font-size: 52px; font-weight: 700; letter-spacing: -1px; margin-bottom: 40px; }
p.sub { font-size: 36px; color: #4b5260; margin-top: 24px; }
p.small { font-size: 24px; color: #6b7280; margin-top: 28px; }
table { border-collapse: collapse; width: 100%; background: #fff; font-size: 34px; }
th, td { border: 1px solid #c9ced6; padding: 22px 28px; text-align: left; vertical-align: middle; }
th { background: #e6e9ee; font-weight: 600; width: 46%; }
td.num { font-weight: 800; font-size: 46px; }
td.good { color: #1f6f43; }
.flow { display: grid; grid-template-columns: repeat(5, 1fr); border: 1px solid #c9ced6; background: #fff; }
.flow div { border-right: 1px solid #c9ced6; padding: 30px 22px; min-height: 300px; }
.flow div:last-child { border-right: none; }
.flow b { display: block; font-size: 36px; margin-bottom: 16px; }
.flow span { display: block; font-size: 28px; color: #2b3240; line-height: 1.5; }
.flow i { display: block; font-style: normal; font-size: 22px; color: #6b7280; margin-top: 18px; }
.check { margin-top: 26px; border: 1px solid #c9ced6; background: #fff; padding: 22px 28px; font-size: 30px; }
"""

SLIDES = {
    "제목": """<h1>원래 어디서?</h1>
<p class="sub">기사 속 숫자의 원문을 문장까지 찾아 주는 AI 에이전트</p>
<p class="small">라이너 .EXE-THON 2026 출품 · 라이너 API로 만들었습니다</p>""",
    "문제_AI": """<h2>AI 검색에 물어보면</h2>
<table><tr><th>AI 검색 8종, 뉴스 인용 출처 시험</th><td class="num">오답 60% 이상</td></tr>
<tr><th>원문 대신 사본·기사 인용, 지어낸 주소</th><td class="num">다수</td></tr></table>
<p class="small">출처: Columbia Journalism Review, Tow Center for Digital Journalism (2025년 3월)</p>""",
    "성적표": """<h2>처음 보는 인용 문장 60개 · 고치기 전 첫 측정</h2>
<table>
<tr><th>원문 확인 없이 단정한 수</th><td class="num good">0</td></tr>
<tr><th>원문 문장까지 찾음 (공개 원문 있는 21개 중)</th><td class="num">13개 (62%)</td></tr>
<tr><th>쓸모 있는 답 (원문 확인 또는 바른 안내)</th><td class="num">82%</td></tr>
<tr><th>같은 19문장, 원문 문장 맞힘</th><td class="num">우리 12 · Deep Research 8</td></tr>
</table>
<p class="small">무작위 기사 100문장 첫 측정: 원문 문장 57% · 쓸모 있는 답 77% · 틀린 단정 3건(이후 일반 규칙으로 막음)</p>""",
    "API": """<h2>라이너 API 7가지를 단계마다 나눠 씁니다</h2>
<div class="flow">
<div><b>나누기</b><span>Model</span><span>Decisions</span><i>기관·숫자·핵심어<br>확인 대상인지</i></div>
<div><b>찾기</b><span>Search</span><span>Scholar</span><span>Deep Research</span><i>기관 누리집 안·원어·논문<br>마지막 후보</i></div>
<div><b>고르기</b><span>Rerank</span><i>원문다운 후보를 앞으로</i></div>
<div><b>읽기</b><span>Gateway</span><i>PDF·HWP·첨부<br>안 읽히는 PDF는 모델이</i></div>
<div><b>판정</b><span>Model</span><span>Decisions</span><i>칸별 비교·교차 확인</i></div>
</div>
<div class="check">단계 사이마다 코드가 원문 문서를 직접 열어 숫자를 확인 → 문서에 없는 인용은 '원문 확인'으로 내지 않음</div>""",
    "마무리": f"""<div style="display:flex;gap:56px;align-items:center">
<div style="flex:1.05">
<p style="font-size:30px;color:#4b5260;margin-bottom:14px">결론</p>
<h2 style="font-size:56px;line-height:1.3">숫자를 옮기기 전에,<br>먼저 넣어 보세요</h2>
<table style="font-size:30px">
<tr><th style="width:38%">찾는 곳</th><td>원문의 <b>쪽과 문장</b>까지</td></tr>
<tr><th>모를 때</th><td>지어내지 않고 <b>확인할 곳</b> 안내</td></tr>
<tr><th>걸리는 시간</th><td><b>30분 → 1분 안에</b></td></tr>
</table>
<p class="small">원래 어디서? · github.com/babymir/wonrae-eodiseo</p>
</div>
<div style="flex:1;height:820px;overflow:hidden;border:1px solid #c9ced6;background:#fff">
<img src="data:image/png;base64,{_b64(RESULT_PIC)}" style="width:100%;display:block">
</div></div>""",
}


def flip_pages() -> None:
    """원 보고서를 1쪽부터 178쪽까지 넘기는 그림들(문제 장면: '수백 쪽을 넘겨야')."""
    if not FLIP_PDF.exists():
        sys.exit(f"오류: 쪽 넘기기에 쓸 PDF 가 없습니다: {FLIP_PDF.relative_to(ROOT)}")
    out = OUT / "쪽넘기기"
    out.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(FLIP_PDF)
    target = min(FLIP_TARGET, doc.page_count)
    pages = sorted({1 + round((target - 1) * (i / (FLIP_FRAMES - 1)) ** 1.6) for i in range(FLIP_FRAMES)})
    for n, p in enumerate(pages):
        pix = doc[p - 1].get_pixmap(matrix=fitz.Matrix(1.6, 1.6))
        pix.save(str(out / f"{n:03d}.png"))
    print(f"쪽 넘기기 그림 {len(pages)}장 (1~{target}쪽)")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        for name, body in SLIDES.items():
            page.set_content(f"<!doctype html><meta charset='utf-8'><style>{CSS}</style><body>{body}</body>")
            page.wait_for_timeout(300)
            page.screenshot(path=str(OUT / f"{name}.png"))
            print(f"그림: {name}.png")
        browser.close()
    flip_pages()
    return 0


if __name__ == "__main__":
    sys.exit(main())
