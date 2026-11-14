"""'기사가 계산'한 숫자 찾기: 계산은 코드가, 고르기는 라이너 모델이.

예전 방식(모델이 식을 지어내고 코드는 재료 문장만 확인)은 실행마다 결과가 흔들렸다
(194만원 = 405.2 − 211.2 를 찾았다 못 찾았다 함, 11/12~13).
지금 방식:
1) 핵심어가 드물게 나오는 쪽 MAX_PAGES 개에서 숫자를 모두 뽑는다(연도 같은 숫자는 뺌)
2) 가까이 있는 숫자 쌍마다 빼기·더하기·나누기·증가율·%p 차를 계산해,
   기사 숫자의 표시 자릿수로 반올림했을 때 같은 식만 남긴다(우연히 맞는 식도 섞임)
3) 가까울수록·단순할수록 점수를 높여 상위 TOP 개를 라이너 모델에 보여 주고, 같은 뜻의 식을 고르게 한다(없으면 0)
"""
import re

from liner_api import ask_json, json_schema
from PDF도구 import keyword_hits

MAX_PAGES = 10
MAX_GAP = 400            # 두 숫자 사이 최대 글자 수(같은 표·같은 문단)
TOP = 5
SNIPPET = 70
NUM = re.compile(r"(?<![\d.,])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\d,])|(?<![\d.,])-?\d+(?:\.\d+)?(?![\d])")
OPS = {  # 이름: (계산, 식 모양, 단순함 점수)
    "빼기": (lambda a, b: a - b, "{a} - {b}", 1.0),
    "더하기": (lambda a, b: a + b, "{a} + {b}", 0.8),
    "나누기(배)": (lambda a, b: a / b if b else None, "{a} ÷ {b}", 0.7),
    "증가율(%)": (lambda a, b: (a - b) / b * 100 if b else None, "({a} - {b}) ÷ {b} × 100", 0.6),
    "비율(%)": (lambda a, b: a / b * 100 if b else None, "{a} ÷ {b} × 100", 0.5),
}
CHOOSE_PROMPT = """기사 숫자 "{number}"가 원문에 그대로는 없다. 코드가 원문 숫자들로 계산해 같은 값이 나오는 식을 찾았다.
기사 문장: "{article}"
후보 식(원문 숫자와 그 주변 글):
{cands}
기사 숫자와 같은 뜻(같은 대상·같은 기간·같은 지표)으로 계산한 식의 번호를 고르라. 우연히 값만 같은 식이면 0.
JSON: {{"선택": 번호 또는 0, "이유": "한 줄"}}"""
CHOOSE_SCHEMA = json_schema("calc_pick", {"선택": {"type": "integer"}, "이유": {"type": "string"}})


def _target(number: str) -> tuple[float, int] | None:
    """'194만원' → (194.0, 0),  '1.3%p' → (1.3, 1). 첫 숫자와 소수 자릿수."""
    m = re.search(r"\d[\d,]*(?:\.(\d+))?", number)
    if not m:
        return None
    return float(m.group(0).replace(",", "")), len(m.group(1) or "")


def _numbers(page: str) -> list[tuple[float, str, int]]:
    out = []
    for m in NUM.finditer(page):
        text = m.group(0)
        value = float(text.replace(",", ""))
        if "." not in text and 1900 <= abs(value) <= 2100:  # 연도
            continue
        if "." not in text and abs(value) < 10:            # 번호·순번이 많아 우연 일치가 큼
            continue
        out.append((value, text, m.start()))
    return out


def _pages(pages: list[str], keywords: list[str]) -> list[int]:
    # '월평균 임금 격차'처럼 붙은 핵심어는 낱말로도 나눠 본다(분해 결과가 실행마다 달라도 같은 쪽을 고르게)
    keywords = list(dict.fromkeys([*keywords, *(w for k in keywords for w in k.split() if len(w) >= 2)]))
    found = {k: {i for i, p in enumerate(pages) if keyword_hits([k], p)} for k in keywords}
    weight = {k: 1 / len(v) for k, v in found.items() if v}
    score = {i: sum(w for k, w in weight.items() if i in found[k]) for i in range(len(pages))}
    return sorted([i for i in score if score[i] > 0], key=lambda i: -score[i])[:MAX_PAGES]


def search(pages: list[str], number: str, keywords: list[str]) -> list[dict]:
    """기사 숫자가 나오는 식 후보(점수순)."""
    target = _target(number)
    if not target:
        return []
    value, decimals = target
    found = []
    for i in _pages(pages, keywords):
        nums = _numbers(pages[i])
        for x, (a, at, ap) in enumerate(nums):
            for b, bt, bp in nums[x + 1:]:
                gap = abs(bp - ap)
                if gap > MAX_GAP:
                    break
                for (p, pt), (q, qt) in (((a, at), (b, bt)), ((b, bt), (a, at))):
                    for name, (fn, shape, simple) in OPS.items():
                        try:
                            got = fn(p, q)
                        except (ZeroDivisionError, OverflowError):
                            continue
                        # 기사 숫자는 크기만 쓰므로(‘19만명 줄어’) 양수 결과만. 순서만 바꾼 식이 중복되지 않음
                        if got is None or got <= 0 or round(got, decimals) != round(value, decimals):
                            continue
                        found.append({"쪽": i + 1, "식": shape.format(a=pt, b=qt) + f" = {got:.{max(decimals, 1)}f}",
                                      "계산": name, "점수": simple - gap / MAX_GAP,
                                      "재료": [{"숫자": pt, "원문": _around(pages[i], pt)},
                                               {"숫자": qt, "원문": _around(pages[i], qt)}]})
    found.sort(key=lambda c: -c["점수"])
    seen, unique = set(), []
    for c in found:   # 같은 식(순서만 다른 것 등)은 하나만
        if c["식"] not in seen:
            seen.add(c["식"])
            unique.append(c)
    return unique


def _around(page: str, text: str) -> str:
    pos = page.find(text)
    return page[max(0, pos - SNIPPET): pos + len(text) + SNIPPET].strip() if pos >= 0 else text


def check(pages: list[str], sentence: str, info: dict) -> dict:
    """{"계산됨", "식", "쪽", "재료", "후보수", "방법"}"""
    cands = search(pages, info["숫자"], info["핵심어"])
    print(f"    계산 찾기: 원문 숫자 조합 중 기사 숫자가 나오는 식 {len(cands)}개")
    if not cands:
        return {"계산됨": False, "후보수": 0, "방법": "코드 계산(조합 없음)"}
    top = cands[:TOP]
    lines = [f"[{n}] {c['쪽']}쪽 {c['식']} ({c['계산']}) — 재료: "
             + " / ".join(f"'{m['숫자']}' 주변: {m['원문']}" for m in c["재료"]) for n, c in enumerate(top, start=1)]
    ans = ask_json(CHOOSE_PROMPT.format(number=info["숫자"], article=sentence, cands="\n".join(lines)),
                   kind="계산 식 고르기", effort="low", schema=CHOOSE_SCHEMA)
    try:
        pick = int(ans.get("선택") or 0)
    except (TypeError, ValueError):
        pick = 0
    if not 1 <= pick <= len(top):
        return {"계산됨": False, "후보수": len(cands), "방법": "코드 계산 + 라이너 모델 고르기(맞는 식 없음)",
                "이유": ans.get("이유", "")}
    c = top[pick - 1]
    return {"계산됨": True, "식": c["식"], "쪽": c["쪽"], "재료": c["재료"], "후보수": len(cands),
            "방법": "코드 계산 + 라이너 모델 고르기", "이유": ans.get("이유", "")}
