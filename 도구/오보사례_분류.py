"""모은 보도 설명자료에서 '기사 숫자를 원문과 대조하면 잡을 수 있는 건'만 골라 평가 대상을 만든다.

라이너 모델이 1차로 뽑고(기사 주장·문제 유형·기관 설명), 사람이 설명 글과 대조해 확정한다
(확정 칸: 평가대상.json 의 '사람확인'). 정답은 기관 설명자료에 적힌 내용이다.

사용: python3 도구/오보사례_분류.py
입력: 작업/평가_오보사례/목록.json (오보사례_모으기.py 결과)
결과: 작업/평가_오보사례/분류.json (한 건마다 저장 → 다시 실행하면 이어서)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from liner_api import ask_json, json_schema  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "작업" / "평가_오보사례"
MIN_TEXT = 200
S = {"type": "string"}
TYPES = ["숫자 자체가 틀림", "시점 혼동", "대상·모집단 혼동", "지표·정의 혼동", "비교 기준 혼동", "단위 혼동",
         "자체 계산·추정", "숫자와 무관(정책·방법·행정 해명)", "기타"]
SCHEMA = json_schema("factcheck_case", {
    "유형": {"type": "string", "enum": TYPES},
    "기사주장": S,       # 설명자료가 인용한 기사 문장(숫자 포함) 그대로
    "기사숫자": S,
    "기관설명": S,       # 무엇이 왜 틀렸는지 한두 문장
    "바른값": S,         # 기관이 제시한 올바른 숫자·표현(없으면 "")
    "원자료": S,         # 기관이 근거로 든 통계·보고서 이름
})
PROMPT = """아래는 통계 기관이 어떤 기사에 대해 낸 '보도 설명자료'다. JSON 하나로만 답하라.
- 유형: 기사의 무엇이 문제였는지 하나를 고른다. 기사 숫자가 원 통계와 다르면 '숫자 자체가 틀림',
  숫자는 맞는데 해석이 문제면 시점·대상·지표·비교 기준·단위 중 하나, 기사가 직접 계산·추정한 수치면 '자체 계산·추정',
  숫자와 상관없는 해명(정책 설명·조사 방법 반박·행정)이면 '숫자와 무관(정책·방법·행정 해명)'.
- 기사주장: 설명자료가 인용한 기사 내용 중 숫자가 든 문장을 그대로(없으면 "").
- 기사숫자: 그 문장의 핵심 숫자. 기관설명: 무엇이 왜 문제인지 한두 문장. 바른값: 기관이 제시한 올바른 값(없으면 "").
- 원자료: 기관이 근거로 든 통계·보고서 이름.
설명자료 제목: {title}
설명자료 본문:
{text}"""


def main() -> None:
    rows = [r for r in json.loads((DIR / "목록.json").read_text(encoding="utf-8")) if len(r.get("설명", "")) >= MIN_TEXT]
    out_path = DIR / "분류.json"
    done = {r["순번"]: r for r in json.loads(out_path.read_text(encoding="utf-8"))} if out_path.exists() else {}
    for r in rows:
        if r["순번"] in done:
            continue
        try:
            ans = ask_json(PROMPT.format(title=r["제목"], text=r["설명"]), kind="오보 사례 분류", effort="low",
                           schema=SCHEMA)
        except RuntimeError as e:  # 한 건 실패는 기록하고 계속
            print(f"  {r['순번']}번 분류 실패: {e}")
            continue
        done[r["순번"]] = {"순번": r["순번"], "보도일자": r["보도일자"], "매체": r["매체"], "제목": r["제목"],
                          "관련보도": r["관련보도"], **ans}
        print(f"  {r['순번']:>3} {r['보도일자']} {ans.get('유형')} | {ans.get('기사주장', '')[:50]}")
        out_path.write_text(json.dumps(sorted(done.values(), key=lambda x: -x["순번"]), ensure_ascii=False, indent=1),
                            encoding="utf-8")
    print(f"분류 {len(done)}건 → {out_path}")


if __name__ == "__main__":
    main()
