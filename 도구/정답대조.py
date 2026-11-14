"""평가 결과를 '정답 찾기'(에이전트·사람이 직접 찾은 공개 원문)와 대조한 채점표(11/14, 11/15 보강).

정답: <세트>/정답찾기/결과_*.json  (분류: 원문있음 / 원문있음_미확인숫자 / 원문비공개 / 대상아님 / 모름)
결과: <세트>/평가결과.json

세 가지로 나눠 센다(판정 이름만으로 '찾음'이라 하지 않는다 — 11/15 진단 보고서):
- 원문 문장 확인: 일치·표현 바뀜·기사가 계산·숫자 다름 + 근거 문서의 기관 주소가 정답과 같음
- 근거 확인 필요: 위 판정이지만 근거 주소가 정답과 다름 → 사람이 보고 <세트>/정답찾기/근거확인.json 에 적는다
  ('같은 원문'이면 원문 문장 확인, '다른 출처'는 공식 2차 문서, '틀린 근거'는 헛답으로 센다)
- 사람 확인 필요: '표현 다름 후보'(같은 숫자인데 기사가 표현을 바꿈)
- 확인 위치 안내: '통계표·원자료 인용'(원문 문장이 아니라 통계표 위치)
못 찾은 건은 단계로 나눈다:
- 후보 단계: 정답 기관의 문서를 읽지도 못함(검색·후보 고르기에서 빠짐)
- 대조 단계: 정답 기관의 문서는 읽었는데 숫자를 못 찾거나 판정에서 떨어짐

사용: python3 도구/정답대조.py 작업/평가_무작위3 [작업/평가_무작위 ...] [--표=작업/정답대조_채점표.md]
"""
import glob
import json
import math
import re
import sys
import urllib.parse
from collections import Counter

QUOTE_FOUND = {"일치", "표현 바뀜", "기사가 계산", "숫자 다름"}
NEAR = "표현 다름 후보"
TABLE = "통계표·원자료 인용"
HAS_ORIGINAL = {"원문있음", "원문있음_미확인숫자"}
SECOND_LEVEL = {"co", "go", "or", "re", "ac", "ne", "pe", "gov", "com", "org", "net", "edu"}   # kr·uk·jp 등 2단계 주소


def site(url: str) -> str:
    """주소의 기관 부분(www.mods.go.kr → mods.go.kr, gnews.gg.go.kr → gg.go.kr, pubmed.ncbi.nlm.nih.gov → nih.gov)."""
    host = urllib.parse.urlparse(url or "").netloc.lower().split(":")[0]
    parts = [p for p in host.split(".") if p]
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def wilson(k: int, n: int) -> tuple[float, float]:
    """95% 윌슨 구간(작은 표본에서 비율의 불확실성)."""
    if n == 0:
        return 0.0, 0.0
    z, p = 1.96, k / n
    mid = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, mid - half), min(1.0, mid + half)


def read_sites(res: dict) -> set[str]:
    """우리가 실제로 읽은(근거·후보) 문서들의 기관 주소."""
    urls = [res.get("상세페이지") or ""]
    urls += [x.get("주소", "") for x in res.get("후보") or []]
    urls += [x.get("상세페이지", "") for x in res.get("다른후보") or []]
    return {site(u) for u in urls if u}


def grade(gt: dict, res: dict, checked: dict | None = None) -> str:
    verdict = res.get("판정", "오류")
    if verdict in QUOTE_FOUND:
        if site(res.get("상세페이지", "")) == site(gt.get("원문주소", "")):
            return "원문 문장 확인"
        return CHECKED.get((checked or {}).get("판단", ""), "근거 확인 필요")
    if verdict == NEAR:
        return "사람 확인 필요"
    if verdict == TABLE:
        return "확인 위치 안내"
    return "못 찾음(대조 단계)" if site(gt.get("원문주소", "")) in read_sites(res) else "못 찾음(후보 단계)"


CHECKED = {"같은 원문": "원문 문장 확인", "다른 출처": "다른 공식 출처", "틀린 근거": "틀린 근거(헛답)"}
ORDER = ["원문 문장 확인", "다른 공식 출처", "틀린 근거(헛답)", "근거 확인 필요", "사람 확인 필요", "확인 위치 안내", "못 찾음(대조 단계)", "못 찾음(후보 단계)"]
MARK = {"원문 문장 확인": "✅", "다른 공식 출처": "☑️", "틀린 근거(헛답)": "⛔", "근거 확인 필요": "🔍", "사람 확인 필요": "🔶", "확인 위치 안내": "📍",
        "못 찾음(대조 단계)": "❌대조", "못 찾음(후보 단계)": "❌후보"}


def score(folder: str) -> tuple[Counter, list[str], Counter]:
    gt = {g["번호"]: g for f in sorted(glob.glob(f"{folder}/정답찾기/결과_*.json")) for g in json.load(open(f))}
    rows = {r["번호"]: r for r in json.load(open(f"{folder}/평가결과.json"))}
    try:   # 사람이 근거를 확인한 기록(없으면 빈 것)
        checks = {c["번호"]: c for c in json.load(open(f"{folder}/정답찾기/근거확인.json"))}
    except FileNotFoundError:
        checks = {}
    counts, others, lines = Counter(), Counter(), []
    for n in sorted(rows):
        g, res = gt.get(n, {}), rows[n].get("결과", {})
        verdict = res.get("판정", "오류")
        if g.get("분류") in HAS_ORIGINAL:
            kind = ("숫자확인" if g["분류"] == "원문있음" else "위치만")
            grade_ = grade(g, res, checks.get(n))
            counts[(kind, grade_)] += 1
            lines.append(f"| {MARK[grade_]} | {folder.split('/')[-1]}#{n} | {kind} | {verdict} | "
                         f"{site(g.get('원문주소', ''))} | {site(res.get('상세페이지', '')) or '-'} | "
                         f"{re.sub(r'[|\\n]', ' ', rows[n]['문장'])[:50]} |")
        else:
            found = verdict in QUOTE_FOUND | {NEAR, TABLE}
            others[(g.get("분류", "정답 없음"), "찾았다고 함" if found else "못 찾음/대상 아님")] += 1
            if found:
                lines.append(f"| ⚠️ | {folder.split('/')[-1]}#{n} | 정답:{g.get('분류')} | {verdict} | - | "
                             f"{site(res.get('상세페이지', '')) or '-'} | {re.sub(r'[|\\n]', ' ', rows[n]['문장'])[:50]} |")
    return counts, lines, others


def summary(counts: Counter, kind: str) -> str:
    n = sum(v for (k, _), v in counts.items() if k == kind)
    if not n:
        return f"{kind}: 0건"
    parts = [f"{g} {counts[(kind, g)]}" for g in ORDER if counts[(kind, g)]]
    strict = counts[(kind, "원문 문장 확인")]
    loose = strict + sum(counts[(kind, g)] for g in ("다른 공식 출처", "근거 확인 필요", "사람 확인 필요", "확인 위치 안내"))
    lo, hi = wilson(strict, n)
    return (f"{kind} {n}건 — 원문 문장 확인 {strict}건 ({strict / n:.0%}, 95% 구간 {lo:.0%}~{hi:.0%}), "
            f"넓게 세면 {loose}건 ({loose / n:.0%}) · " + " · ".join(parts))


if __name__ == "__main__":
    folders = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--표=")), "")
    total, all_lines, all_others = Counter(), [], Counter()
    for folder in folders:
        counts, lines, others = score(folder)
        total.update(counts)
        all_others.update(others)
        all_lines += lines
        print(f"\n## {folder}\n- " + summary(counts, "숫자확인") + "\n- " + summary(counts, "위치만"))
    report = ["# 정답 대조 채점표", "", "분모: 정답 찾기에서 공개 원문이 있다고 한 문장('숫자확인' = 원문에서 숫자까지 확인, "
              "'위치만' = 조회 화면·접속 차단으로 위치만 앎). '원문 문장 확인'은 근거 문서의 기관 주소가 정답과 같은 것만.", "",
              "- " + summary(total, "숫자확인"), "- " + summary(total, "위치만"),
              "- 정답에 원문이 없는 문장: " + ", ".join(f"{k[0]}·{k[1]} {v}" for k, v in sorted(all_others.items())), "",
              "| 결과 | 문장 | 정답 종류 | 우리 판정 | 정답 기관 | 우리 근거 기관 | 문장 |", "|---|---|---|---|---|---|---|",
              *all_lines]
    print("\n## 합계\n- " + summary(total, "숫자확인") + "\n- " + summary(total, "위치만"))
    print("- 정답에 원문이 없는 문장: " + ", ".join(f"{k[0]}·{k[1]} {v}" for k, v in sorted(all_others.items())))
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(report) + "\n")
        print(f"\n채점표: {out}")
