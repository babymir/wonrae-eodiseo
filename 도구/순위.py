"""라이너 Rerank·Decisions 로 순위 매기기와 판정 교차 확인.

- 후보 보고서 순위: 기사가 밝힌 보고서 이름·핵심어와 후보 제목(+표지 첫머리)을 Rerank 로 비교
- 숫자 후보 순위: 기사 문장과 원문 후보 문단을 Rerank 로 비교(숫자만 같은 다른 지표를 뒤로)
- 판정 교차 확인: 라이너 모델이 내린 판정을 Decisions 모델이 한 번 더 보고, 다르면 '사람 확인 권장'
Rerank·Decisions 가 실패하면 기존 방식(글자 비교·모델 판정)으로 계속한다(이유는 출력).
"""
from liner_api import decide_many, rerank

CROSS_MIN_CONFIDENCE = 0.5   # 교차 확인 확신도가 이보다 낮으면 사람 확인 권장
AXIS_DIFF_MIN = 0.5          # 축별 질문에서 '다름' 확률이 이보다 높으면 그 축은 다르다고 봄
AXES = {"기간": "두 문장의 숫자가 가리키는 기간(연도·월·분기·비교 기준)",
        "대상": "두 문장의 숫자가 가리키는 대상(모집단·집단·지역)",
        "단위": "두 문장의 숫자 단위(%, %p, 명, 원, 배 등과 자릿수 단위)",
        "지표": "두 문장의 숫자가 나타내는 지표의 정의(무엇을 센 값·비율인지)"}
VERDICT_CRITERIA = {
    "일치": "기사 숫자와 원문 숫자가 같은 지표·대상·기간·단위를 가리킨다(반올림·띄어쓰기·단위 환산 차이는 같음으로 본다)",
    "표현 바뀜": "같은 숫자지만 기간·대상·단위·용어 중 하나 이상의 뜻이 원문과 달라졌다",
    "다른 지표": "숫자만 같고 원문에서는 다른 지표나 다른 대상의 값이다",
}


def rerank_scores(query: str, texts: list[str]) -> list[float] | None:
    """texts 각각의 관련도 점수(원래 순서대로). 실패하면 None."""
    try:
        ranked = rerank(query, texts)
    except RuntimeError as e:
        print(f"    Rerank 실패, 글자 비교로 계속: {e}")
        return None
    scores = [0.0] * len(texts)
    for idx, score in ranked:
        if 0 <= idx < len(texts):
            scores[idx] = score
    return scores


def rank_hits(sentence: str, hits: list[dict]) -> list[dict]:
    """숫자 후보 문단을 기사 문장과 가까운 순으로. 같은 점수면 기존 핵심어 점수 순서를 유지."""
    if len(hits) <= 1:
        return hits
    scores = rerank_scores(sentence, [h["원문"] for h in hits])
    if scores is None:
        return hits
    for h, s in zip(hits, scores):
        h["관련도"] = round(s, 3)
    print(f"    라이너 Rerank 로 숫자 후보 {len(hits)}곳 순위 매김 (1위 관련도 {max(scores):.2f})")
    return sorted(hits, key=lambda h: -h["관련도"])


def cross_check(sentence: str, quote: str, verdict: str) -> dict:
    """라이너 Decisions 로 판정을 한 번 더. 판정 질문 1개 + 기간·대상·단위·지표 축별 질문 4개를 한 요청에.
    (질문끼리 서로의 답을 보지 않으므로 축마다 따로 확인됨)
    결과: {판정, 확률, 확신도, 같음, 축: {기간: 같음|다름 …}, 다른축, 사람확인권장}"""
    questions = {"판정": {"instructions": "기사 문장의 숫자가 원문 문장의 숫자와 같은 뜻인지 판정하라.",
                         "criteria": VERDICT_CRITERIA}}
    for axis, what in AXES.items():
        questions[axis] = {"instructions": f"{what}이(가) 기사와 원문에서 같은가? 표기·반올림 차이는 같음으로 본다.",
                           "criteria": {"같음": "같다", "다름": "다르다"}}
    try:
        ans = decide_many({"기사 문장": sentence, "원문 문장": quote}, questions)
    except RuntimeError as e:
        print(f"    Decisions 교차 확인 실패: {e}")
        return {}
    main = ans["판정"]
    choice, conf = main.get("choice"), float(main.get("confidence") or 0)
    same = choice == verdict
    axes, different = {}, []
    for axis in AXES:
        prob = float((ans[axis].get("probabilities") or {}).get("다름", 0))
        axes[axis] = f"다름 {prob:.2f}" if prob > AXIS_DIFF_MIN else f"같음 {1 - prob:.2f}"
        if prob > AXIS_DIFF_MIN:
            different.append(axis)
    # 모델은 '일치'인데 축별 확인에서 다른 축이 나오면 사람 확인 권장
    review = (not same) or conf < CROSS_MIN_CONFIDENCE or (verdict == "일치" and bool(different))
    out = {"판정": choice, "확률": main.get("probabilities", {}), "확신도": round(conf, 2), "같음": same,
           "축": axes, "다른축": different, "사람확인권장": review}
    print(f"    라이너 Decisions 교차 확인: {choice} (확신도 {conf:.2f}) → {'같음' if same else '다름'}"
          + (f", 축별로 다름: {'·'.join(different)}" if different else ""))
    return out


SAME_MIN = 0.5   # '같은 지표' 확률이 이보다 낮으면 '숫자 다름'을 참고로 낮춘다


def same_indicator(sentence: str, quote: str) -> dict:
    """'숫자 다름' 교차 확인: 기사 숫자와 원문 숫자가 같은 지표·대상·시점을 가리키는지 라이너 Decisions 에 묻는다.
    맞는 기사를 틀렸다고 하는 것이 가장 위험하므로, 판정 모델과 별개로 한 번 더 확인한다.
    결과: {같은지표확률, 통과} (실패하면 통과 False — 확인 못 했으면 '숫자 다름'을 내지 않음)"""
    try:
        ans = decide_many({"기사 문장": sentence, "원문 문장": quote}, {"같은지표": {
            "instructions": "기사 문장의 숫자와 원문 문장의 숫자가 같은 지표·같은 대상·같은 시점을 가리키는가? 값이 다른 것은 상관없다.",
            "criteria": {"같은 지표": "같은 것을 잰 숫자다", "다른 지표": "지표·대상·시점 중 하나라도 다르다"}}})
    except RuntimeError as e:
        print(f"    Decisions '같은 지표' 확인 실패: {e}")
        return {"같은지표확률": None, "통과": False}
    prob = float((ans["같은지표"].get("probabilities") or {}).get("같은 지표", 0))
    print(f"    라이너 Decisions '같은 지표' 확인: {prob:.2f}")
    return {"같은지표확률": round(prob, 2), "통과": prob >= SAME_MIN}
