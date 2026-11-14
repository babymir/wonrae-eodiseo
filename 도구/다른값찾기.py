"""'숫자 다름' 찾기: 기사 숫자가 원문에 없을 때, 원문에 '같은 지표·대상·시점'의 다른 숫자가 있는지 본다.

왜: 국가데이터처가 해명한 실제 오보(예: 조생양파 재배면적 '48% 감소' ↔ 원문 24.4% 감소)를
    지금까지는 '원문에 없음'으로만 답해, 원문이 실제로 뭐라고 하는지 보여 주지 못했다(11/13 오보 사례 0/3).
방법(2단 판정과 같은 원칙): 핵심어가 나오는 원문 대목을 모아 라이너 모델에 '같은 지표의 숫자'를 고르게 하고,
    코드가 ① 인용이 PDF 글자에 실제로 있는지 ② 그 숫자가 기사 숫자의 어떤 표기와도 다른지
    ③ 지표·대상·시점 칸이 모두 같음/동의어인지 확인한 뒤에만 '숫자 다름'으로 본다.
    맞는 기사를 틀렸다고 하는 것이 가장 위험하므로, 조금이라도 애매하면 None(→ '원문에 없음').
"""
import re
from pathlib import Path

from liner_api import PDF_MAX_BYTES, ask_json, json_schema
from PDF도구 import quote_in_pages, squash
from 숫자표기 import to_regex, variants

WINDOW = 220          # 핵심어 앞뒤로 볼 글자 수
MAX_SNIPPETS = 10     # 모델에 보여 줄 대목 수
IMAGE_PDF_CHARS = 200 # 쪽 글자 합이 이보다 적으면 그림(스캔) PDF 로 본다
S = {"type": "string"}
_AX = {"type": "string", "enum": ["같음", "동의어", "다름", "알수없음"]}
SCHEMA = json_schema("other_value", {
    "있음": {"type": "boolean"}, "대목": {"type": "integer"}, "원문인용": S, "원문숫자": S,
    "지표": _AX, "대상": _AX, "시점": _AX})
PROMPT = """기사 숫자 "{number}"가 원 보고서에 글자 그대로는 없다. 아래 원문 대목 중에
기사와 **같은 지표·같은 대상·같은 시점**을 가리키는 다른 숫자가 있는지 찾아라. JSON 하나로만 답하라.
기사: "{article}"
원문 대목:
{snippets}
규칙:
- "있음": 같은 지표·대상·시점의 숫자가 원문에 있으면 true. 비슷한 주제일 뿐 지표나 대상이 다르면 false.
- "대목": 그 숫자가 있는 대목 번호. "원문인용": 그 숫자가 나온 원문 문장을 글자 그대로. "원문숫자": 그 숫자(단위 포함).
- "지표"·"대상"·"시점": 기사와 비교해 같음/동의어/다름/알수없음. 확인할 수 없으면 "알수없음".
- 확신이 없으면 "있음": false."""


def _snippets(pages: list[str], keywords: list[str]) -> list[tuple[int, str]]:
    """핵심어가 나오는 곳 앞뒤 WINDOW 글자 중 숫자가 든 대목(쪽, 글). 핵심어가 많이 든 대목부터."""
    found = []
    for no, text in enumerate(pages, start=1):
        for k in keywords:
            for m in re.finditer(re.escape(k), text):
                part = re.sub(r"\s+", " ", text[max(0, m.start() - WINDOW): m.end() + WINDOW])
                if re.search(r"\d", part):
                    score = sum(1 for kk in keywords if squash(kk) in squash(part))
                    found.append((score, no, part))
    found.sort(key=lambda x: -x[0])
    out, seen = [], set()
    for _, no, part in found:
        key = (no, part[:60])
        if key not in seen:
            seen.add(key)
            out.append((no, part))
        if len(out) >= MAX_SNIPPETS:
            break
    return out


def find(pages: list[str], sentence: str, number: str, keywords: list[str]) -> dict | None:
    """{"쪽", "원문인용", "원문숫자", "칸"} 또는 None."""
    parts = _snippets(pages, keywords)
    if not parts:
        return None
    lines = "\n".join(f"[{i}] {no}쪽: \"{text}\"" for i, (no, text) in enumerate(parts, start=1))
    try:
        ans = ask_json(PROMPT.format(number=number, article=sentence, snippets=lines),
                       kind="같은 지표 다른 숫자", effort="low", routing="quality", schema=SCHEMA)
    except RuntimeError as e:  # 실패하면 '원문에 없음' 그대로(이유 출력)
        print(f"    같은 지표 다른 숫자 찾기 실패: {e}")
        return None
    if not ans.get("있음"):
        return None
    quote, value = (ans.get("원문인용") or "").strip(), (ans.get("원문숫자") or "").strip()
    axes = {k: ans.get(k) for k in ("지표", "대상", "시점")}
    if not quote or not value or any(v not in ("같음", "동의어") for v in axes.values()):
        print(f"    같은 지표 다른 숫자: 칸 확인이 애매해 버림 {axes}")
        return None
    page = quote_in_pages(quote, pages)
    digits = re.search(r"\d[\d,]*(?:\.\d+)?", value)
    if page is None or not digits or digits.group(0) not in quote:
        print(f"    같은 지표 다른 숫자: 인용이 원문에 없거나 숫자가 인용에 없어 버림: {quote[:50]}")
        return None
    if any(to_regex(v.text).search(value) for v in variants(number)):
        return None  # 사실 같은 숫자(표기만 다름) → '숫자 다름'이 아님
    return {"쪽": page, "원문인용": quote, "원문숫자": value, "칸": axes}


IMAGE_PROMPT = """첨부한 PDF는 기사가 인용한 원 보고서 후보다(글자를 뽑을 수 없는 그림 PDF).
기사 숫자 "{number}"가 이 PDF에 없을 때, 기사와 **같은 지표·같은 대상·같은 시점**을 가리키는 다른 숫자가 있는지 찾아라.
기사: "{article}"
JSON 하나로만 답하라. "있음": true/false, "대목": 쪽 번호(1부터), "원문인용": 그 숫자가 나온 문장·표 줄을 보이는 그대로,
"원문숫자": 그 숫자(단위 포함), "지표"·"대상"·"시점": 기사와 비교해 같음/동의어/다름/알수없음. 확신이 없으면 "있음": false."""


def find_in_image_pdf(pdf: Path, sentence: str, number: str) -> dict | None:
    """그림 PDF: 게이트웨이 모델이 PDF 를 직접 보고 같은 지표의 다른 숫자를 찾는다.
    글자 대조로 검증할 수 없으므로 결과에 '검증' 경고를 붙인다(화면이 '직접 확인 필요'로 보여 줌)."""
    if pdf.stat().st_size > PDF_MAX_BYTES:
        return None
    try:
        ans = ask_json(IMAGE_PROMPT.format(number=number, article=sentence), pdf_path=pdf,
                       kind="같은 지표 다른 숫자(그림 PDF)", effort="low", schema=SCHEMA)
    except RuntimeError as e:
        print(f"    그림 PDF 다른 숫자 찾기 실패: {e}")
        return None
    axes = {k: ans.get(k) for k in ("지표", "대상", "시점")}
    value = (ans.get("원문숫자") or "").strip()
    if not ans.get("있음") or not value or any(v not in ("같음", "동의어") for v in axes.values()):
        return None
    if any(to_regex(v.text).search(value) for v in variants(number)):
        return None
    try:
        page = int(ans.get("대목") or 0) or None
    except (TypeError, ValueError):
        page = None
    return {"쪽": page, "원문인용": (ans.get("원문인용") or "").strip(), "원문숫자": value, "칸": axes,
            "검증": "그림 PDF라 원문 글자와 대조하지 못했습니다. 원문 쪽을 직접 확인하세요."}


def is_image_pdf(pages: list[str]) -> bool:
    return sum(len(p.strip()) for p in pages) < IMAGE_PDF_CHARS
