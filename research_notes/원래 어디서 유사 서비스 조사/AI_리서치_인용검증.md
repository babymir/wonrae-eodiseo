# 범용 AI 리서치 도구·인용 검증 도구가 「원래 어디서?」의 일을 이미 하는가 (2026-11 기준)

> 조사일: 2026-11-12. 웹 검색·페이지 확인 약 17회. 범용 AI 제품(Perplexity, ChatGPT, Gemini, Claude 등)의 공식 문서는 직접 열어 보지 못했다. 그래서 이 도구들의 기능 설명 중 출처가 없는 것은 "추론" 또는 "공백"으로 따로 적었다.
> 표기: **[확인]** = 출처가 있는 사실, **[추론]** = 확인한 사실에서 끌어낸 해석

---

## Q1. 범용 AI 리서치 도구(Perplexity, ChatGPT, Gemini/NotebookLM, Claude, Liner, Genspark)는 기사 속 숫자를 1차 출처 문서의 쪽·문장까지 추적하고, 의미 변형과 기자 계산 수치를 잡아내는가?

### Takeaway
이 도구들은 모두 "답변 문장마다 출처 링크를 다는" 일은 한다. 하지만 기사 속 숫자를 **1차 출처(정부 보고서·보도자료)의 쪽과 원문 문장까지 거슬러 올라가는 기능**, **기간·대상·단위·용어가 바뀌었는지 판정하는 기능**, **기자가 계산한 숫자를 재료 숫자로 풀어 보이는 기능**은 어느 제품에서도 확인하지 못했다. 독립 연구를 보면 이 도구들이 인용하는 쪽은 대체로 2차 자료이고, 출처가 실제로 주장을 뒷받침하는 비율도 40~80%에 그친다.

### Cited Findings

**Liner (해커톤 주최사, 겹치는 기능을 가장 꼼꼼히 봐야 함)**
- [확인] Liner CEO는 2026년 7월 인터뷰에서 강점을 "심층 리서치와 출처 검증"으로 꼽았다. 생성한 거의 모든 문장에 웹·학술 DB 출처 링크를 달고, 답변 옆에 출처 원문을 함께 보여 준다고 설명했다. — [Liner 뉴스(ITChosun 인터뷰)](https://liner.com/news/Liner-ITChosun-AI2026)
- [확인] Liner는 SimpleQA 사실성 점수 95.3을 내세운다. 회사가 직접 발표한 수치이고 독립 감사는 아니다. — [Liner 뉴스](https://liner.com/news/Liner-AI-based-Education-Solution-of-the-Year)
- [확인] Liner는 API로 "문장 단위 출처 표시"를 제공한다. SKT 에이닷이 Liner의 검색·인용 계층을 쓴다. Search API는 결과마다 출처를 붙이고, 웹 요청 1,000건당 1달러다. — [Liner Build 블로그](https://build.liner.com/blog/liner-adot-api-case-study)
- [확인] Liner의 Citation Recommender는 인용이 빠진 초안을 올리면 문장마다 후보 논문을 추천한다. 추천 순서는 관련성, 최신성, 피인용 수, 학술지 영향력으로 정한다. 출처가 그 주장을 실제로 담고 있는지 확인한다는 설명은 페이지에 없다. 대상은 "학술 인용"과 논문이고, 정부 보고서를 다루는지는 나오지 않는다. — [Liner Citation Recommender](https://liner.com/features/research-agents/citation-recommender)
- [확인] 같은 페이지에 나오는 Liner 리서치 에이전트 목록은 Hypothesis Generator, Hypothesis Evaluator, Literature Review, **Research Tracer**, Survey Generator, Survey Simulator, Peer Review다. "주장 검증(claim check)" 에이전트는 목록에 없다. — [Liner Citation Recommender](https://liner.com/features/research-agents/citation-recommender)
- [확인] Liner 마케팅 문구에는 "줄 단위 인용으로 Liner 답변을 쉽게 팩트체크하라", "인용을 직접 하나하나 확인하라"가 있다. 검증하는 사람은 사용자다. — [Liner 검색](https://liner.com/search), [Liner Learn](https://liner.com/learn/citation-recommender)
- [확인] "Source Verification"이나 "claim check"라는 이름의 Liner 기능은 검색으로 찾지 못했다. — 검색 결과 기준

**NotebookLM (Google)**
- [확인] 인용을 누르면 사용자가 올린 문서의 해당 구절(PDF 안 위치)로 이동한다. 200쪽짜리 10-K 문서로 시험한 리뷰 1건에서는 정확도가 높았다. 수학 계산, 지저분한 PDF, 아주 큰 문서의 세부 검색에는 약하다는 리뷰도 있다. 복사 방지 PDF는 가져오지 못한다는 안내도 있다. 체계적인 정확도 벤치마크는 찾지 못했다. — [howaiworks.ai](https://howaiworks.ai/ai-tools/notebooklm.md), [techwolf](https://ai-first.techwolf.ai/course/notebooklm), [fone.tips](https://fone.tips/how-to-use-notebooklm/)

**Genspark**
- [확인] Genspark는 한국어 페이지로 "AI 팩트체크 — 어떤 주장이든 즉시 검증하세요" 도구를 제공한다. 페이지가 403으로 막혀 기능 세부(쪽·문장 제시, 판정 등급)는 확인하지 못했다. — [Genspark AI 팩트체커](https://www.genspark.ai/ko/tools/ai-fact-checker)

**Deep Research 계열 전반 (독립 평가)**
- [확인] DeepTRACE(ICLR 2026, Venkit·Laban 등)는 GPT-5 Deep Research, YouChat, Perplexity, Copilot Think Deeper, Gemini, GPT-5 웹검색을 감사했다. 결과는 다음과 같다.
  - 인용 정확도: 생성형 검색엔진 40~68%, 전체 40~80%
  - 논쟁 질문에 한쪽 입장만 담은 답변: 50~95%
  - 근거 없는 문장 비율: GPT-5 DR 12.5%, Perplexity DR 97.5%
  - Bing Chat: 출처 목록의 3분의 1 이상이 본문에서 실제로 인용되지 않음
  - 출처: [Liner 리뷰 페이지(AI 생성+편집)](https://liner.com/review/deeptrace-auditing-deep-research-ai-systems-for-tracking-reliability-across). 원 논문을 직접 확인하지는 않았다.
- [확인] "Cited but Not Verified"(arXiv 2605.06635, 2026-05)는 LLM 14종의 딥리서치 보고서 인용을 세 기준으로 평가했다.
  - 링크 작동: 94% 이상
  - 주제 관련성: 80% 이상
  - 출처와 사실 일치(Fact Check): 39~77%에 그침
  - 도구 호출을 2회에서 150회로 늘리면 사실 일치 정확도가 평균 약 42% 떨어졌다.
  - Claude Opus 4.5가 사실 일치 76.8%로 가장 높았다. OpenAI 모델은 인용 수는 많지만 정확도가 낮았다.
  - 출처: [arXiv HTML](https://arxiv.org/html/2605.06635), [Emergent Mind](https://www.emergentmind.com/papers/2605.06635)
- [확인] 2026년 7월 arXiv 논문 "Do You Need a Frontier Model as a Citation Verifier?"는 인용 검증기 자체를 다룬 연구다. 세부 내용은 확인하지 못했다. — [arXiv 2607.08700](https://arxiv.org/abs/2607.08700)

### Inferences
- [추론] 범용 도구가 하는 일은 "답변 문장 → 출처 URL" 연결이다. 우리 제품의 일은 반대 방향이다. "기사 속 숫자 → 1차 출처 PDF의 쪽·문장 → 의미 변형 유형 판정"으로 거슬러 올라간다. 이 역방향 추적과 6가지 판정(일치 / 표현 변경 / 기자 계산 / 통계표 인용 / 출처에 없음 / 못 찾음)을 하는 범용 제품은 확인되지 않았다.
- [추론] NotebookLM은 사용자가 이미 원본 PDF를 갖고 있을 때 "쪽·구절 확인"에 가장 가깝다. 하지만 원본을 **찾는** 단계와 **변형을 판정**하는 단계는 없다. 이 두 단계가 우리 제품의 차별점이다.
- [추론] Liner와 겹치는 지점은 "문장 단위 인용 + 원문 함께 보기"라는 UX 원칙이다. Liner는 이 원칙을 AI 답변 검증에 쓴다. 우리는 같은 원칙을 **사람(기자)이 쓴 문장 검증**에 쓴다. 해커톤에서는 "Liner의 인용 철학을 뉴스 숫자로 넓혔다"고 위치를 잡는 것이 겹침을 강점으로 바꾸는 방법이다. Liner 검색·인용 API를 출처 탐색 단계에 실제로 쓸 수 있는지 검토할 만하다.
- [추론] 사용자가 범용 도구에 "이 숫자 원출처 찾아줘"라고 프롬프트로 시키는 것은 가능하다. 그러나 아래 Q3의 연구를 보면 결과는 2차 자료에서 멈추기 쉽고, 변형 판정은 일관되지 않을 것이다.

### Gaps
- Perplexity Deep Research, ChatGPT deep research/search, Gemini Deep Research, Claude research의 공식 문서(인용 단위, PDF 쪽 앵커 지원 여부)는 이번에 직접 확인하지 못했다. "쪽 단위 앵커를 지원하지 않는다"는 판단은 확인된 사실이 아니다.
- 이 도구들의 한국어 정부 보고서(KDI, KIHASA, 통계청 보도자료 등) 추적 성능을 다룬 평가는 찾지 못했다.
- Liner의 "Research Tracer" 에이전트가 무엇을 하는지(인용 계보 추적인지)는 확인하지 못했다. **주최사 기능과 겹칠 수 있으므로 직접 확인이 필요하다.**
- Genspark 팩트체커의 실제 동작과 출력 형식은 확인하지 못했다(페이지 403).

---

## Q2. 학술 도구(Consensus, Scite, Elicit, Semantic Scholar)와 인용·환각 검사기는 "출처가 그 주장을 실제로 담고 있는지" 확인하는가?

### Takeaway
학술 도구는 **논문끼리의 인용 관계**(Scite)나 **문헌 전체가 주장을 지지하는지**(Consensus)를 본다. 인용 검사기(GPTZero)는 **참고문헌이 실제로 존재하는지**를 본다. 특정 숫자가 특정 문서의 어느 쪽·문장에 있는지, 또 어떻게 바뀌었는지 판정하는 도구는 확인되지 않았다. 대상도 영문 학술 논문이 중심이어서 한국 정부 보고서와 보도자료는 범위 밖이다.

### Cited Findings
- [확인] **Scite Smart Citations**는 한 논문을 인용한 다른 논문의 인용 문장을 뽑아 "지지 / 반박 / 단순 언급"으로 기계학습 분류한다. 출판사와 색인 계약을 맺어 전문 PDF·XML을 확보한다. — [Scite 도움말](https://help.scite.ai/zh/article/how-does-scite-work-1eje9i9), [docs.scite.ai](https://docs.scite.ai/concepts/citation-model.md)
- [확인] Scite가 밝힌 규모는 Smart Citation 16억 건 이상, 색인 논문 3.17억 편 이상(2026-09)이다. 바이오·생명과학 분야에 가장 강하다는 리뷰가 있다. "Reference Check" 기능이 있다고 하지만 동작 방식은 확인하지 못했다. — [educatorstechnology 2026 리뷰](https://www.educatorstechnology.com/2026/09/scite-review.html)
- [확인] **Consensus**는 예/아니오 질문에 대해 논문들의 지지·반대 분포를 보여 주는 Consensus Meter를 제공한다. 주장을 붙여 넣으면 피어리뷰 문헌과 대조한다는 리뷰도 있다. 전문(full text) 접근 여부는 리뷰마다 말이 다르다. — [atlasworkspace 비교](https://www.atlasworkspace.ai/blog/consensus-vs-elicit), [pasqualepillitteri 가이드](https://pasqualepillitteri.it/en/news/1291/consensus-ai-search-engine-220-million-scientific-papers-guide-2026)
- [확인] **Elicit**는 PDF를 올려 질의응답하고 문장 단위로 근거를 추적할 수 있다. 체계적 문헌고찰과 데이터 추출에 강하고, 빠른 주장 확인용은 아니라고 평가된다. — [casrai 비교](https://casrai.org/compare/elicit-vs-consensus)
- [확인] **GPTZero Hallucination Check**는 참고문헌을 Google Scholar, PubMed, arXiv, CrossRef, DOI/URL과 대조해 **존재하지 않거나 뒤섞인 인용**을 찾는다.
  - NeurIPS 2025 채택 논문 4,841편에서 환각 인용 100건 이상(51~53편)을 찾았다.
  - ICLR 2026 제출 논문에서도 50건 이상을 찾았다.
  - 정확도 99% 이상은 회사가 직접 밝힌 수치다.
  - 출처: [GPTZero NeurIPS 조사](https://gptzero.me/news/neurips/), [Fortune 2026-01-21](https://fortune.com/2026/01/21/neurips-ai-conferences-research-papers-hallucinations/), [The Decoder](https://the-decoder.com/over-100-fake-citations-slip-through-peer-review-at-top-ai-conference/)
- [확인] 학계 연구로는 SemanticCite(arXiv 2511.16198)가 있다. 인용된 논문 전문을 읽고, 인용 주장이 그 논문으로 뒷받침되는지 근거와 함께 판정하는 연구 프로토타입이다. — [Emergent Mind](https://api.emergentmind.com/papers/2511.16198)
- [확인] Meta AI는 위키피디아 인용이 주장을 실제로 뒷받침하는지 검사하고 더 나은 출처를 추천하는 모델(SIDE)을 공개했다. 이것도 연구 단계다. — [arXiv 2207.06220](https://arxiv.org/abs/2207.06220), [Analytics India](https://analyticsindiamag.com/meta-ai-launches-an-open-sourced-model-to-make-wikipedia-entries-more-accurate/)
- [확인] 한국 국가데이터처(구 통계청)는 2026-07-08부터 KOSIS MCP 시범서비스를 운영한다. 국가승인통계 1,360여 종과 통계표 26만여 개를 Claude, Gemini, ChatGPT에 연결해 일상언어로 검색할 수 있다. 정식 서비스는 2027년 예정이다. KOSIS에 없는 자료를 쓰면 부정확할 수 있고, 결과는 참고용이라고 안내한다. — [dazabi(보도자료 전재)](https://dazabi.com/insurance_magazine/article.php?id=34025)

### Inferences
- [추론] 이 도구들을 우리 판정 범주에 대 보면 이렇다.
  - GPTZero: "출처 자체가 존재하는가"만 본다.
  - Scite·SemanticCite: "인용 문장과 인용된 논문이 맞는가"를 본다. 우리의 "일치 / 출처에 없음" 판정과 개념이 가장 가깝다.
  - "기간·대상·단위·용어 변경", "기자 계산 수치(재료 숫자 제시)", "통계표 원자료 인용" 같은 **숫자 특화 판정**을 하는 도구는 없다.
- [추론] KOSIS MCP는 경쟁자라기보다 **부품**이다. "통계표·원자료에서 인용" 판정을 할 때 KOSIS 통계표를 직접 조회하는 데 쓸 수 있다.
- [추론] 법률 인용 검사기(Westlaw KeyCite, Lexis Shepard's 등)는 "판례가 아직 유효한가 + 인용이 맞는가"를 보는 오래된 사례다. 전문 분야에서는 인용 검증이 별도 제품군으로 자리 잡았다는 근거로 쓸 수 있다. 다만 이번 조사에서는 검색으로 확인하지 않았다.

### Gaps
- Semantic Scholar에 주장-원문 대조 기능이 있는지는 확인하지 못했다.
- Scite "Reference Check"와 Consensus "Claim Verification"의 공식 동작 설명(벤더 문서)은 확인하지 못했다. 근거는 제3자 리뷰뿐이다.
- 법률 인용 검사기(KeyCite, Shepard's, 2023 이후 AI 판례 환각 사건)는 이번 범위에서 출처를 확보하지 못했다.
- 한국어 뉴스 수치를 검증하는 전용 서비스(SNU팩트체크 등 언론사 도구 포함)는 검색으로 찾지 못했다. 머니투데이 기사(2026-09-01)는 팩트체커의 수작업 원문 확인 절차만 다뤘다. — [머니투데이](https://www.mt.co.kr/tech/2026/09/01/2026083115312341480)

---

## Q3. AI 도구는 통계를 인용할 때 1차 출처 대신 2차 출처를 얼마나 자주 쓰는가? (근거 연구)

### Takeaway
2026년 10월 Trakkr 연구 결과는 이렇다. AI가 숫자 근거로 인용한 페이지의 82%에 그 숫자가 있었지만, **원 연구 자체인 페이지는 21%뿐**이었다. 끝까지 따라가 같은 내용의 연구에 닿은 숫자는 40%였다. 원 연구에 닿은 숫자의 17%는 중간에 연도·대상·측정 대상 등이 바뀌어 있었다. 이 연구는 우리 제품이 푸는 문제("2차 자료에서 멈춤", "전달 과정의 의미 변형")를 정면으로 수치화했다.

### Cited Findings
- [확인] **Trakkr "Zombie stats" (Research Study 013, 2026-10-28, Mack Grenfell)**
  - 대상: ChatGPT, Perplexity, Gemini, Claude, Google AI Overviews
  - 질문: 숫자를 요구하는 질문 200개(API, 웹검색 켬, 미국 위치)
  - 수집: 통계 3,167개 중 인용이 붙은 2,419개, 그중 1,285개를 무작위로 골라 최대 5단계까지 추적
  - 결과: 원출처 확인(같은 내용) 40%(95% 구간 34~46%), 변형됨 8%, 확인 불가 40%, 출처 없음 11%
  - 인용된 페이지에 숫자가 있었던 비율 82%, 그중 원 연구 페이지 21%
  - 원 연구에 닿은 숫자 중 17%가 변형. 변형 유형은 숫자 26%, 측정 대상 20%, 연도 17%, 출처 16%, 조사 대상 14%, 장소 4%
  - AI 판정기만 쓰면 원출처 확인이 23%로 나와서, 150건은 사람이 직접 점검해야 했다.
  - 도구별 원출처 확인율: ChatGPT 57%(답변당 통계 1.3개), Perplexity 46%(2.2개), AI Overviews 39%, Gemini 38%, Claude 38%(6.8개). 뒤 넷은 오차 범위가 겹쳐 순위를 매기지 않았다.
  - 첫 인용 출처 유형: 기업 블로그 54%(원출처 확인 37%), 뉴스 10%(37%), 통계 모음 8%(35%), 정부 7%(**61%**), 리서치 기관 13%(62%)
  - 5개 도구 모두 퍼센트를 낸 71개 질문 중 28개에서 값이 4~5가지로 갈렸다(전형적 차이 16%p).
  - 출처: [Trakkr Research](https://trakkr.ai/trakkr-research/zombie-stats)
  - 주의: Trakkr는 AI 노출 최적화 업체다. 이 연구는 업체 자체 연구이고 피어리뷰는 받지 않았다.
- [확인] **Columbia Tow Center (CJR, 2025-03)**
  - 대상: ChatGPT Search, Perplexity, Perplexity Pro, DeepSeek, Copilot, Grok-2, Grok-3, Gemini
  - 방법: 기사 발췌문 200개로 1,600회 질의해, 원 기사·매체·URL을 맞히는지 평가
  - 결과: 전체적으로 60% 이상 틀렸다(Perplexity 37%, Grok 3 94%; 도구별 수치는 2차 보도 기준). 신디케이션된 복사본을 인용하거나 링크를 지어냈다. 모른다고 하지 않고 확신에 찬 오답을 냈고, 유료 버전이 더 자신 있게 틀렸다.
  - 출처: [CJR Tow Center](https://www.cjr.org/tow_center/we-compared-eight-ai-search-engines-theyre-all-bad-at-citing-news.php), [TechSpot](https://www.techspot.com/news/107101-new-study-finds-ai-search-tools-60-percent.html)
- [확인] **EBU·BBC (2025-10)**
  - 대상: 18개국 22개 공영방송, 4개 AI 비서
  - 결과: 답변의 45%에 중대한 문제가 1개 이상 있었다. 31%는 출처 표기가 누락·오도·오류였고, 20%는 환각이나 오래된 정보를 담았다. Gemini는 76%에 문제가 있었고 주원인은 출처 표기였다. BBC의 이전 조사보다 중대 문제 비율은 51%에서 37%로 줄었다. 오류는 언어·도구·시장 전반에 걸친 구조적 문제로 평가됐다.
  - 출처: [Advanced Television](https://www.advanced-television.com/2025/10/22/study-ai-misrepresents-news-content-45-of-the-time), [TV Technology](https://www.tvtechnology.com/news/major-study-finds-high-levels-of-mistakes-in-ai-generated-news-summaries), [MediaPost](https://www.mediapost.com/publications/article/410114/ai-assistants-provide-answers-full-of-errors-stud.html)
- [확인] 딥리서치의 인용 사실 일치율은 39~77%이고, 검색을 많이 할수록 오히려 떨어진다(arXiv 2605.06635). DeepTRACE의 인용 정확도는 40~80%다(Q1 참고). — [arXiv 2605.06635](https://arxiv.org/html/2605.06635)

### Inferences
- [추론] Trakkr의 변형 유형(연도 17%, 조사 대상 14%, 측정 대상 20%, 숫자 26%)은 우리의 "표현 변경(기간·대상·단위·용어)" 판정 범주와 거의 같다. 피치 자료에서 "AI도 숫자의 원출처를 21%만 직접 인용하고, 원출처에 닿아도 17%는 바뀌어 있다"는 문제 정의 근거로 쓸 수 있다(업체 연구라는 점은 밝혀야 한다).
- [추론] 정부 출처는 원출처 확인율이 61%로 가장 높은 축에 든다. 우리가 정부 연구기관 보고서와 통계청 보도자료로 범위를 좁힌 것은 성공률 면에서 합리적이다. 반대로 AI 판정기만으로는 23%밖에 못 잡았다는 결과는, **사람이 확인할 수 있게 쪽·원문 문장을 보여 주는 UI**가 필수라는 근거다.
- [추론] 세 연구(Tow, EBU/BBC, Trakkr)는 모두 영어권·유럽 중심이다. 한국어 뉴스와 한국 정부 보고서에서 같은 문제가 얼마나 생기는지는 데이터가 없다. 우리 제품이 시연 사례로 직접 수치를 보여 주면 그것이 차별 포인트가 된다.

### Gaps
- 한국어 AI 답변과 한국 뉴스의 통계 원출처 추적률을 다룬 연구는 찾지 못했다.
- "기자가 직접 계산한 숫자"가 얼마나 흔한지 다룬 연구는 찾지 못했다. Trakkr의 "숫자 변경 26%"가 일부 해당할 수 있지만, 계산인지 오기인지는 구분되어 있지 않다.
- Tow Center 도구별 오류율(Perplexity 37% 등)은 2차 보도(eWeek, TechSpot) 기준이다. CJR 원문에서 다시 확인하지 않았다.
- EBU·BBC 보고서 원문(PDF)은 열어 보지 않았다. 수치는 보도 기준이다.
