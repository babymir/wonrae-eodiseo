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
        targets = {site(gt.get("원문주소", ""))} | {site("https://" + a) for a in gt.get("대체주소") or []}
        if site(res.get("상세페이지", "")) in targets:   # 같은 원문의 다른 곳(대체주소)도 인정
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


def _links(res: dict) -> list[str]:
    """결과가 사용자에게 보여 준 모든 주소(근거·후보·안내·통계표)."""
    urls = [res.get("상세페이지") or ""]
    urls += [x.get("주소", "") for x in res.get("후보") or []]
    urls += [x.get("상세페이지", "") for x in res.get("다른후보") or []]
    urls += [x.get("주소", "") for x in (res.get("안내") or {}).get("링크") or []]
    urls += [x.get("주소", "") for x in res.get("통계표후보") or []]
    return [u for u in urls if u]


def three_metrics(folder: str) -> dict:
    """새 목표(11/15 밤)의 세 지표: ① 틀린 단정 ② 원문 문장 확인률 ③ 쓸모 있는 답 비율."""
    gt = {g["번호"]: g for f in sorted(glob.glob(f"{folder}/정답찾기/결과_*.json")) for g in json.load(open(f))}
    rows = {r["번호"]: r["결과"] for r in json.load(open(f"{folder}/평가결과.json"))}
    try:
        checks = {c["번호"]: c for c in json.load(open(f"{folder}/정답찾기/근거확인.json"))}
    except FileNotFoundError:
        checks = {}
    wrong, useful, checkable, quote_n, quote_ok, notes = [], 0, 0, 0, 0, []
    by_source_q, by_source_u = {}, {}   # 같은 원문은 한 번만: 원문 → 그 원문을 인용한 문장들이 모두 맞았는지
    for n, res in sorted(rows.items()):
        g = gt.get(n, {})
        kind, verdict = g.get("분류"), res.get("판정", "오류")
        claimed = verdict in QUOTE_FOUND | {NEAR}
        target = site(g.get("원문주소", ""))
        grade_ = grade(g, res, checks.get(n)) if kind in HAS_ORIGINAL else ""
        # ① 틀린 단정
        if claimed and kind in ("원문비공개", "대상아님") and (checks.get(n) or {}).get("판단") != "같은 원문":
            wrong.append(f"#{n} 정답 '{kind}'인데 '{verdict}'")
        if grade_ == "틀린 근거(헛답)":
            wrong.append(f"#{n} 틀린 근거")
        if verdict == "원문에 없음" and kind in HAS_ORIGINAL:
            wrong.append(f"#{n} 원문이 있는데 '원문에 없음'")
        # ② 원문 문장 확인률
        if kind == "원문있음":
            quote_n += 1
            quote_ok += grade_ == "원문 문장 확인"
            key = source_key(g, n)
            by_source_q[key] = by_source_q.get(key, True) and grade_ == "원문 문장 확인"
        # ③ 쓸모 있는 답
        if kind in ("원문있음", "원문있음_미확인숫자", "원문비공개"):
            checkable += 1
            if kind in HAS_ORIGINAL:
                targets = {target} | {site("https://" + a) for a in g.get("대체주소") or []}   # 같은 원문의 다른 곳
                ok = grade_ in ("원문 문장 확인", "다른 공식 출처") or (
                    bool(target) and any(site(u) in targets for u in _links(res)) and grade_ != "틀린 근거(헛답)")
                how = "원문 확인" if grade_ in ("원문 문장 확인", "다른 공식 출처") else "확인 위치 안내" if ok else ""
            else:
                ok = not claimed and bool((res.get("안내") or {}).get("유형") or res.get("출처종류") == "의원실 자료")
                how = "비공개 종류 안내" if ok else ""
            useful += bool(ok)
            key = source_key(g, n)
            by_source_u[key] = by_source_u.get(key, True) and bool(ok)
            notes.append(f"#{n} [{kind}] {'✅ ' + how if ok else '❌'} ({verdict})")
    return {"틀린단정": wrong, "원문확인": (quote_ok, quote_n), "쓸모": (useful, checkable), "건별": notes,
            "원문확인_원문별": (sum(by_source_q.values()), len(by_source_q)),
            "쓸모_원문별": (sum(by_source_u.values()), len(by_source_u))}


def source_key(g: dict, n: int) -> str:
    """정답 원문을 가리키는 열쇠(주소에서 http·www·끝 / 를 뗌). 원문 주소가 없으면 문장마다 따로.
    (11/16: 무작위 세트에 같은 BLS PPI 발표를 인용한 문장이 8건 — 문장 수로 세면 한 원문이 성적을 끌어올림)"""
    url = (g.get("원문주소") or "").strip().lower()
    url = re.sub(r"^https?://(www\.)?", "", url).rstrip("/")
    return url or f"#{n}"


def metrics_text(m: dict) -> str:
    qk, qn = m["원문확인"]
    uk, un = m["쓸모"]
    lo, hi = wilson(qk, qn)
    ulo, uhi = wilson(uk, un)
    return (f"① 틀린 단정 {len(m['틀린단정'])}건{(' — ' + ', '.join(m['틀린단정'])) if m['틀린단정'] else ''}\n"
            f"② 원문 문장 확인 {qk}/{qn} ({qk / max(qn, 1):.0%}, 95% 구간 {lo:.0%}~{hi:.0%}) — 목표 50%\n"
            f"③ 쓸모 있는 답 {uk}/{un} ({uk / max(un, 1):.0%}, 95% 구간 {ulo:.0%}~{uhi:.0%}) — 목표 80%"
            + _per_source(m))


def _per_source(m: dict) -> str:
    """같은 원문을 인용한 문장이 여럿이면, 원문마다 한 번만 센 값도(그 문장들이 모두 맞아야 맞음)."""
    qk, qn = m["원문확인_원문별"]
    uk, un = m["쓸모_원문별"]
    if (qn, un) == (m["원문확인"][1], m["쓸모"][1]):
        return ""
    return (f"\n   같은 원문은 한 번만 세면: ② {qk}/{qn} ({qk / max(qn, 1):.0%}) · ③ {uk}/{un} ({uk / max(un, 1):.0%})"
            " — 한 원문의 문장이 모두 맞아야 맞음")


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
    print("\n## 새 목표 세 지표(세트별)")
    for folder in folders:
        print(f"[{folder}]\n" + metrics_text(three_metrics(folder)))
    print("- 정답에 원문이 없는 문장: " + ", ".join(f"{k[0]}·{k[1]} {v}" for k, v in sorted(all_others.items())))
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(report) + "\n")
        print(f"\n채점표: {out}")
