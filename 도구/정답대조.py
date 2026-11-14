"""평가 결과를 '정답 찾기'(사람·에이전트가 직접 찾은 공개 원문)와 대조해 원문 찾은 비율을 낸다(11/14).

정답: <세트>/정답찾기/결과_*.json  (분류: 원문있음 / 원문있음_미확인숫자 / 원문비공개 / 대상아님 / 모름)
찾음: 우리 판정이 일치·표현 바뀜·기사가 계산·통계표·원자료 인용 (근거 주소·인용은 사람이 따로 확인)
사용: python3 도구/정답대조.py 작업/평가_무작위3 [작업/평가_무작위 ...]
"""
import glob
import json
import sys
from collections import Counter

FOUND = {"일치", "표현 바뀜", "기사가 계산", "통계표·원자료 인용", "표현 다름 후보"}
HAS_ORIGINAL = {"원문있음", "원문있음_미확인숫자"}


def score(folder: str) -> tuple[int, int, list[str]]:
    gt = {g["번호"]: g for f in sorted(glob.glob(f"{folder}/정답찾기/결과_*.json")) for g in json.load(open(f))}
    rows = {r["번호"]: r for r in json.load(open(f"{folder}/평가결과.json"))}
    hit, total, lines, other = 0, 0, [], Counter()
    for n in sorted(rows):
        g, res = gt.get(n, {}), rows[n].get("결과", {})
        verdict = res.get("판정", "오류")
        if g.get("분류") in HAS_ORIGINAL:
            total += 1
            ok = verdict in FOUND
            hit += ok
            mark = "🔶" if verdict == "표현 다름 후보" else "✅" if ok else "❌"
            lines.append(f"{mark} #{n} [{verdict}] 정답 {g.get('원문종류')} {str(g.get('원문주소'))[:70]}\n"
                         f"      우리: {str(res.get('상세페이지') or '')[:80]}\n      인용: {str(res.get('원문인용') or '')[:120]}")
        else:
            other[(g.get("분류"), verdict in FOUND)] += 1
            if verdict in FOUND:
                lines.append(f"⚠️ #{n} 정답 {g.get('분류')}인데 우리 [{verdict}] {str(res.get('원문인용') or '')[:100]}")
    lines.append(f"원문 없는 건: {dict(other)}")
    return hit, total, lines


if __name__ == "__main__":
    H = T = 0
    for folder in sys.argv[1:]:
        h, t, lines = score(folder)
        H, T = H + h, T + t
        print(f"\n## {folder}: 공개 원문 있는 {t}건 중 찾음 {h}건 ({h / max(t, 1):.0%})")
        print("\n".join(lines))
    if len(sys.argv) > 2:
        print(f"\n## 합계: {T}건 중 {H}건 ({H / max(T, 1):.0%})")
