"""라이너 Deep Research 와 같은 문장으로 비교(11/15).

결선 예상 질문 "라이너 Deep Research 로 되지 않나요?"에 같은 문장·같은 정답표로 답한다.
- 라이너 Deep Research API: POST https://platform.liner.com/api/v1/agents/deep-research (stream=false)
- 질문: 기사 문장 + "이 숫자가 처음 나온 원문 주소와 원문 속 문장"
- 채점(정답대조.py 와 같은 기준): 답의 참고 주소 중 정답 원문과 같은 기관 주소가 있는지(1순위인지),
  답한 원문 문장에 기사 숫자(표기 변환 포함)가 있는지
대상: <세트>/정답찾기/결과_*.json 에서 공개 원문이 있는 문장(원문있음·원문있음_미확인숫자)
결과: <세트>/딥리서치비교.json (한 건마다 저장 → 다시 실행하면 이어서), 표는 화면에 출력
사용: python3 도구/딥리서치_비교.py 작업/평가_검증세트
"""
import glob
import json
import sys
import time
import urllib.request
from pathlib import Path

from liner_api import _api_key
from 숫자표기 import to_regex, variants
from 정답대조 import HAS_ORIGINAL, site

URL = "https://platform.liner.com/api/v1/agents/deep-research"
TIMEOUT_SEC = 600
QUESTION = ("다음 한국어 기사 문장 속 숫자가 처음 나온 원 출처 문서(보고서·보도자료·공시·통계표·논문 등)를 찾아 줘. "
            "그 문서의 주소와, 그 문서 안에서 숫자가 나온 문장을 그대로 인용해 줘. 기사는 원 출처가 아니다.\n문장: \"{s}\"")


def ask(sentence: str) -> dict:
    body = json.dumps({"messages": [{"role": "user", "content": QUESTION.format(s=sentence)}],
                       "lang": "ko", "stream": False}).encode()
    req = urllib.request.Request(URL, data=body, method="POST",
                                 headers={"x-api-key": _api_key(), "Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
            data = json.loads(r.read())
    except Exception as e:  # 한 건 실패는 기록하고 다음 건으로(이유 남김)
        return {"오류": f"{type(e).__name__}: {e}", "초": round(time.time() - t)}
    refs = [{"제목": x.get("title"), "주소": x.get("url")} for x in data.get("references") or []]
    return {"답": data.get("answer", ""), "참고": refs, "초": round(time.time() - t)}


def grade(gt: dict, sentence: str, number: str, out: dict) -> dict:
    target = site(gt.get("원문주소", ""))
    sites = [site(r.get("주소") or "") for r in out.get("참고", [])]
    pats = [to_regex(v.text) for v in variants(number)] if number else []
    return {"정답기관": target, "참고에정답기관": target in sites, "1순위가정답기관": bool(sites) and sites[0] == target,
            "참고수": len(sites), "답에숫자": any(p.search(out.get("답", "")) for p in pats)}


def main(folder: str) -> None:
    gt = {g["번호"]: g for f in sorted(glob.glob(f"{folder}/정답찾기/결과_*.json")) for g in json.load(open(f))}
    targets = {t["번호"]: t for t in json.load(open(f"{folder}/평가대상.json"))}
    ours = {r["번호"]: r for r in json.load(open(f"{folder}/평가결과.json"))} if Path(f"{folder}/평가결과.json").exists() else {}
    path = Path(folder) / "딥리서치비교.json"
    done = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for n, g in sorted(gt.items()):
        if g.get("분류") not in HAS_ORIGINAL or str(n) in done:
            continue
        sentence = targets[n]["문장"]
        number = ((ours.get(n) or {}).get("결과", {}).get("추출") or {}).get("숫자", "")
        print(f"#{n} 라이너 Deep Research 질문 중… {sentence[:50]}", flush=True)
        out = ask(sentence)
        out["채점"] = grade(g, sentence, number, out) if "오류" not in out else {}
        done[str(n)] = out
        path.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"    {out.get('초')}초, 참고 {len(out.get('참고', []))}건, 채점 {out.get('채점') or out.get('오류')}", flush=True)
    rows = [v for v in done.values() if v.get("채점")]
    if rows:
        k = len(rows)
        print(f"\n## 라이너 Deep Research ({folder}, {k}건)")
        print(f"- 참고 주소에 정답 원문 기관이 있음: {sum(r['채점']['참고에정답기관'] for r in rows)}/{k}")
        print(f"- 첫 참고 주소가 정답 원문 기관: {sum(r['채점']['1순위가정답기관'] for r in rows)}/{k}")
        print(f"- 답에 기사 숫자가 나옴: {sum(r['채점']['답에숫자'] for r in rows)}/{k}")
        print(f"- 평균 {sum(r['초'] for r in rows) / k:.0f}초")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "작업/평가_검증세트")
