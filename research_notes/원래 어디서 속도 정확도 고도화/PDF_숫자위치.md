# PDF 안에서 특정 통계 숫자(숫자+맥락) 위치를 빠르고 정확하게 찾는 방법

> 작성 2026-11-13. 이미 `reports/원래 어디서 약점 보완 고도화.md`에 들어간 내용(쪽 단위 숫자 깨짐 감지 → 쪽 이미지를 Gemini로, Apple Vision OCR 교차 확인, `insert_pdf`로 쪽 잘라 보내기+원본 쪽 번호 대응표, `find_tables()`+단위 환산 비교, TAT-QA 척도, QuanTemp 분해, OpenDataLoader, Rerank로 후보 정렬)은 반복하지 않고, **새 수치·새 기법만** 적었다. 현재 코드 기준: PyMuPDF 1.27.1 설치됨, pdfplumber 없음, PDF 읽기 모델 `google/gemini-3.8-flash`(라이너 게이트웨이, `도구/liner_api.py`).

## 1. 한국어 표가 든 PDF용 문서 파싱 도구 (품질·속도·비용·로컬/API)

### Takeaway
1주 안에 넣을 만한 것은 "PyMuPDF(이미 있음) + 필요한 쪽만 Docling 또는 Upstage Document Parse"다. Docling은 M3 Max에서 쪽당 약 1.3초(OCR 끄면 60% 단축)로 로컬 도구 중 가장 빠르다고 보고됐고, Upstage는 쪽당 약 $0.01(Standard)~$0.03(Enhanced)로 표 점수(TEDS 92~93)가 공개 벤치마크 상위다. 다만 **한국어 표에 대한 공개 비교 수치는 찾지 못했다** → 우리 실패 PDF 몇 개로 직접 재야 한다.

### Cited Findings
- **Docling 속도(논문, 89개 PDF, CPU 8스레드)**: 쪽당 평균 x86 CPU 3.1초, **MacBook Pro M3 Max 1.26~1.27초**, L4 GPU 0.48초. M3 Max 중앙값 0.32초, 95분위 6.48초(표가 꽉 찬 쪽이 느림). OCR 끄면 런타임 60% 감소, 표 구조 인식 끄면 16% 감소, 둘 다 끄면 약 75% 감소. PDF 글자 파싱(docling-parse)만은 M3 Max 쪽당 44ms. TableFormer(fast)는 M3 Max에서 표 하나당 704ms. EasyOCR은 M3 Max 쪽당 5초로 가장 비쌈 — [Docling 논문 arXiv 2501.17887](https://arxiv.org/pdf/2501.17887)
- **같은 논문의 도구 비교(GPU 없이)**: Docling 3.1초/쪽(x86)·1.27초/쪽(M3 Max), MinerU 3.3초/쪽(x86), Unstructured 4.2초(x86)·2.7초(M3 Max), **Marker 16초 이상(x86)·4.2초(M3 Max)**. MinerU는 M3 Max에서 돌리지 못했다고 적음. GPU에서는 MinerU 0.21초 < Docling 0.49초 < Marker 0.86초 — [arXiv 2501.17887](https://arxiv.org/pdf/2501.17887) (Docling 팀 자체 측정이라 편향 가능)
- **Marker 2(2026-07, Datalab 자체 발표)**: B200 GPU 한 장에서 balanced 모드 2.9쪽/초, MinerU pipeline 0.54쪽/초, Docling 2.1쪽/초. `--disable_ocr` fast 모드는 CPU만으로 23.7쪽/초라고 주장 — [MarkTechPost](https://www.marktechpost.com/2026/07/24/datalab-marker-v2-vs-mineru-docling-and-liteparse-benchmark-breakdown/) (벤더 측정, 한 장비·한 문서 묶음 기준)
- **OmniDocBench**: v1.6부터 Overall = ((1−텍스트 편집거리)×100 + 표 TEDS + 수식 CDM)/3. 상위권은 PaddleOCR-VL, MinerU 2.5 등 파이프라인 방식이라는 정리가 있으나 현재 버전의 Docling·Marker 표 TEDS 값은 확인 못 함. v1.0 기준 OCR 기반 RapidTable 표 TEDS 약 82.5, 범용 VLM 약 71~74 — [OmniDocBench 논문](https://arxiv.org/html/2412.07626v2), [Emergent Mind 요약](https://www.emergentmind.com/topics/omnidocbench) (요약 사이트는 2차 자료)
- **Upstage Document Parse**: DP-Bench(200문서) 리더보드(2024-10)에서 TEDS 92.06, TEDS-S 93.81, NID 96.23, **문서당 평균 3.79초**(AWS Textract 86.39 TEDS, 14.47초) — [DP-Bench 데이터 카드](https://huggingface.co/datasets/upstage/dp-bench). Upstage 블로그는 TEDS 93.48로 Google·MS보다 5점 이상 높다고 주장 — [Upstage 블로그](https://www.upstage.ai/blog/en/let-llms-read-your-documents-with-speed-and-accuracy). 제3자 논문(NovaLAD)도 Upstage TEDS 93.48로 2위로 인용 — [arXiv 2603.00122](https://arxiv.org/pdf/2603.00122). DP-Bench 문서는 미국 의회도서관·OER 위주라 **한국어 표 성능의 직접 근거는 아님**.
- **Upstage 가격**: Standard 쪽당 약 $0.01, Enhanced(VLM 기반, 복잡한 표·차트용) 쪽당 약 $0.03이라는 정리가 다수. 공식 가격표 직접 확인은 못 함, 프로모션 가격 시기 차이 있음 — [Activepieces 정리](https://www.activepieces.com/blog/upstage-api-pricing-all-models-and-costs-2026), [AWS Marketplace](https://aws.amazon.com/marketplace/pp/prodview-x6enp3hgvrqhc)
- **규칙 기반 표 도구 비교(과학 PDF, 2025-11)**: Camelot은 PubTables 0.25, Table-arXiv 0.33, ICDAR-2013 0.83. PyMuPDF·pdfplumber가 PubTables·Table-arXiv에서 Camelot보다 높고, ICDAR-2013에서는 PyMuPDF가 Camelot을 약간 앞섬. 규칙 기반(Camelot·PyMuPDF·pdfplumber)이 딥러닝 모델보다 훨씬 빠름 — [arXiv 2511.16134](https://arxiv.org/pdf/2511.16134)
- **문서 종류별(재무보고서·매뉴얼·법령 등) 비교**: Camelot은 입찰 문서, PyMuPDF는 매뉴얼에서 좋았고, 테두리 없는 표·복잡한 표는 규칙 기반 도구 모두 약함, 표 검출 최고는 딥러닝 TATR — [arXiv 2410.09871](https://arxiv.org/abs/2410.09871v2)
- **Naver CLOVA OCR**: 한국어 인쇄 문서 97~99% 인식이라는 주장은 한국 벤더 리뷰 글의 마케팅 수준 수치로 검증 불가 — [lido 한국어 OCR 글](https://www.lido.app/kr/hangugeo-ocr)
- **PaddleOCR 한국어 함정**: 최신 기본 모델 PP-OCRv6는 한국어 미지원, 한국어는 PP-OCRv5로 떨어짐 — [위키독스 PaddleOCR 실측](https://wikidocs.net/441124)
- **한국어 OCR 벤치마크 KOCRBench / KLOCR**: KLOCR이 단어 정확도 94.6%, CER 2.34%로 보고(오픈소스 모델 비교, CLOVA·Upstage는 비교 대상 아님) — [alphaXiv KOCRBench](https://www.alphaxiv.org/benchmarks/kl-net/kocrbench), [arXiv 2510.02543](https://arxiv.org/html/2510.02543v1)

### Inferences
- 우리 실패 원인(숫자가 그림 글자/글꼴 깨짐)에는 Docling·Marker의 "글자 레이어 파싱"이 소용없다. 이 도구들도 결국 OCR(EasyOCR 등)로 떨어지고 그게 가장 느리다(M3 Max 쪽당 5초). → **전체 PDF 변환에 쓰지 말고 "숫자 후보가 있을 법한 쪽 1~5개"에만** 쓰는 게 맞다.
- 표 칸에 숫자가 나뉜 문제는 Docling(TableFormer)이나 Upstage가 표를 HTML/마크다운 칸 단위로 주므로 "행 머리글·열 머리글·값" 결합이 쉬워진다. 1주 일정에선 **Upstage Enhanced를 실패 쪽에만 호출**(쪽당 3센트, 설치 없음, 한국 회사 → 한국어 PDF에 유리할 가능성)하는 쪽이 Docling 설치·모델 다운로드보다 위험이 작다. 단, 라이너 API 활용 점수와는 무관하니 "보조 도구"로만 표기.
- pdfplumber 추가 설치는 이득이 작다(PyMuPDF `find_tables`와 성능 비슷하거나 낮음).

### Gaps
- 한국어 국책연구원·통계 보도자료 PDF로 Docling·MinerU·Marker·Upstage를 같은 기준에서 비교한 공개 결과는 찾지 못했다.
- Upstage 공식 가격표·파일 크기 한도(28MB PDF 처리 가능 여부)를 1차 자료로 확인하지 못했다.
- CLOVA OCR의 2025~26 가격·한국어 정확도의 독립 측정치 없음.

## 2. 차트·그림 속 숫자 (벡터 차트 라벨, 쪽 영역 OCR, 쪽 이미지 vs PDF 전체를 VLM에)

### Takeaway
차트 숫자는 (1) 벡터 차트면 PyMuPDF `rawdict`로 글자 코드부터 확인 → 정상이면 그대로 좌표와 함께 뽑고, (2) 깨졌으면 그 영역만 고해상도로 렌더링해 OCR/VLM에. Gemini 3 계열은 PDF 한 쪽이 기본 560토큰(+원문 글자)이고 "medium에서 품질이 포화"라 **잘라 보낸 PDF 몇 쪽**이 쪽 이미지보다 싸고 글자 레이어도 같이 들어가 유리하다. VLM 차트 읽기는 값 추출은 꽤 정확하지만 계산은 자주 틀린다.

### Cited Findings
- 글꼴에 ToUnicode가 없으면 PyMuPDF로 복구 불가, OCR이 대안. 진단은 `get_text("rawdict")`로 글자 코드 확인(0xFFFD나 엉뚱한 코드면 PDF 자체 문제) — [PyMuPDF FAQ](https://pymupdf.readthedocs.io/en/latest/faq/)
- 쪽 대부분은 멀쩡하고 일부 글자(예: 숫자)만 깨질 수 있음 — [MuPDF 포럼](https://forum.mupdf.com/t/any-idea-what-is-wrong-with-this-pdf/47)
- **OCR이 드문드문한 글자를 놓침**: PyMuPDF 기본 전체 쪽 OCR(`get_textpage_ocr`)은 한 양식에서 483자, Tesseract를 `psm 11`(sparse text)로 직접 부르면 703자(40% 더) — [PyMuPDF issue #3318](https://gh.r-mj.com/pymupdf/PyMuPDF/issues/3318). 차트 라벨은 드문드문한 글자의 전형.
- `ocrmac`(Apple Vision 래퍼, 최신 1.0.1 2026-01): 글자+경계 상자 반환, 'fast'/'accurate' 수준, 언어 지정 가능. 한국어 정확도 공개 수치는 없음 — [PyPI ocrmac](https://pypi.org/project/ocrmac). Apple Live Text 공식 언어에 한국어 포함(블로그 정리) — [CJK OCR on Mac 블로그](https://kaedeeeeeeeeee.github.io/OCR_LLM/blog/ocr-asian-languages-on-mac/)
- **Gemini 3/4 미디어 토큰**: 이미지 기본 1120토큰(low 280, medium 560, high 1120, ultra_high 2240). **PDF 기본 560토큰**, low/medium/high = 280/560/1120 **+ Native Text**. 문서는 medium 권장, "품질은 대체로 medium에서 포화, high로 올려도 일반 문서 OCR은 거의 안 좋아짐". 한 요청 안에서 항목별로 해상도 다르게 지정 가능 — [Gemini 미디어 해상도 문서](https://aistudio.google.com/docs/media-resolution). Gemini 2.5는 PDF 쪽당 256토큰+원문 글자 또는 OCR(검색 요약 기준) — [ai.google.dev 미디어 해상도](https://ai.google.dev/gemini-api/docs/media-resolution)
- **VLM 차트 읽기 오류 양상**: 차트 값은 대체로 맞게 뽑지만 그 위의 계산이 자주 틀림, 외부 도구 없이 해결 어려움 — [arXiv 2403.12596](https://arxiv.org/pdf/2403.12596). DePlot은 항목명·값 추출 실패 약 1.6%·0.8%로 거의 정확하지만 합계 질문 실패 45.8%, 평균+비교 65.2% — [arXiv 2406.10085](https://arxiv.org/html/2406.10085v2)
- ChartQA 정답 판정은 정답의 ±5% 이내면 맞음(relaxed accuracy) → 라벨 수준 작은 환각을 가릴 수 있음 — [ChartQA arXiv 2203.10244](https://arxiv.org/pdf/2203.10244)
- **자기 앙상블(self-ensembling)**: 한 VLM에서 표를 여러 번 뽑아 칸별 중앙값을 취하면 WB-ChartExtract에서 최대 23% 상대 향상. ChartQA는 값 라벨이 찍힌 단순 차트라 포화 — [benchmarklist 요약](https://benchmarklist.com/benchmarks/self_ensembling_vision_language_models_for_chart_data_extraction/) (집계 사이트, 원문 미확인)

### Inferences
- **벡터 차트 라벨 추출 순서(구현안)**: ① `page.get_text("rawdict")`에서 숫자 모양 span(`[\d.,]+%?`)과 bbox를 모은다 → ② 같은 쪽 `page.get_drawings()`로 막대·선 위치를 보고 가장 가까운 라벨을 연결(선택) → ③ 숫자 span이 0개인데 "단위: %·천명" 글자나 축 눈금이 있으면 "그림 글자 쪽"으로 보고 해당 그림 영역(`page.get_image_info()`나 drawings의 bbox 합집합)을 `get_pixmap(dpi=300, clip=rect)`로 잘라 ocrmac 또는 Gemini에 보낸다. 쪽 전체보다 영역만 보내면 글자가 커져 OCR 정확도가 오르고 토큰도 준다.
- 이미 게이트웨이에 PDF를 보내고 있으므로 **이미지보다 잘라 만든 PDF가 기본(560토큰+글자)**. 그림 글자 쪽만 PNG(clip, dpi 200~300)로 추가하는 혼합이 비용·정확도 균형이 좋다. (토큰 수는 게이트웨이가 Gemini 기본값을 쓴다는 가정. 확인 필요)
- 차트 숫자는 "기사 숫자와 같은 값이 차트에 있는지"만 물으면 되므로 계산 약점(합계·평균)은 거의 문제 안 됨. 다만 기사가 계산한 경우("A−B")는 코드로 계산한다.

### Gaps
- 한국어 보고서 차트(엑셀에서 내보낸 벡터 차트)에 대한 VLM 숫자 읽기 정확도 수치는 없음.
- 라이너 게이트웨이가 `media_resolution` 같은 Gemini 고유 옵션을 통과시키는지 문서로 확인 못 함 → 실측 필요.
- ocrmac 한국어 숫자(쉼표·소수점) 인식률 공개 수치 없음.

## 3. 숫자 맞추기: 반올림·단위(천명/만명/%p/배)·순위 허용 비교, 통계 주장 검증 연구

### Takeaway
연구들은 숫자 비교를 LLM에 맡기지 않고 **"주장 분해 → 근거 검색 → 코드/SQL로 값 비교"**로 푼다. 우리는 이미 단위 환산 비교(이전 보고서)가 계획에 있으므로, 추가로 넣을 것은 **허용 오차 규칙(표시 자릿수 기준 반올림 일치)**과 **'%↔%p', '배↔%' 구분 규칙**이다. 이에 대한 표준 라이브러리는 없다.

### Cited Findings
- CheckThat! 2025 Task 3(숫자 주장 검증): 영어 주장 15,514개, 주장마다 BM25 상위 100개 근거 문서, 라벨 True/False/Conflicting. 근거의 단위(granularity)와 모델 적응이 성능을 좌우 — [ClaimIQ arXiv 2509.11492](https://arxiv.org/html/2509.11492v1), [CEUR](https://ceur-ws.org/Vol-4038/paper_61.pdf)
- 표 문서 주장 검증 MACE(2026-04): Planner·Executor·Verifier 에이전트, 예시로 메가리터 단위 표에 대해 % 주장을 검증 — [arXiv 2604.17225](https://arxiv.org/html/2604.17225)
- OECD 표 434개 기반 주장 검증: 기준선은 OECD DB에 SQL을 작성해 값 확인, 실패의 상당수는 검색 실패로 'Not Enough Information'이 나옴 — [arXiv 2601.17232](https://www.arxiv.org/pdf/2601.17232)
- Full Fact AI는 주장 탐지·주장 매칭에서 성과가 컸고, 공식 통계(국가통계청)가 주 근거. 숫자 매칭의 구체 알고리즘은 공개 자료에서 찾지 못함 — [Full Fact FAQ](https://www.fullfact.org/about/frequently-asked-questions), [IOS Press 2023](https://content.iospress.com/articles/statistical-journal-of-the-iaos/sji230020), [Full Fact NSO 가이드](https://fullfact.github.io/national-statistics-for-fact-checking/docs/fact-checking)
- ChartQA 계열은 ±5% 허용 비교를 쓴다 — [arXiv 2203.10244](https://arxiv.org/pdf/2203.10244) (우리 용도엔 너무 느슨, 아래 추론 참고)

### Inferences (구현 제안, 근거 문헌 없음 → 우리 설계)
- **표시 자릿수 기준 일치**: 기사 숫자의 소수 자릿수 d를 보고 원문값을 d자리로 반올림해 같으면 '일치'(예: 기사 43% ↔ 원문 43.0%/42.96%). ±5% 같은 비율 허용은 쓰지 말 것(3.0%↔3.1%를 같다고 보는 오판).
- **정수 환산 키**: 모든 후보를 (값×단위배수, 단위 종류)로 바꿔 사전 인덱스를 만든다. "867만 1천명"→8,671,000명, "8,671(천명)"→8,671,000명. 표의 단위는 표 제목·머리글의 "(단위: 천명, %)"를 정규식으로 읽는다. 이것으로 쪽별 후보를 O(1) 조회 → 속도도 빨라진다.
- **반올림 후 단위 바뀜 허용**: "약 870만명" ↔ 8,671천명은 '근사 일치'로 별도 표시(유효숫자 2자리 비교).
- **%와 %p**: 기사 "3%p 상승" ↔ 원문 "3.0%p" 일치, 원문이 "3.0% 증가"면 '표현 바뀜' 후보. 배수: "2배" ↔ "100% 증가"는 같은 값(배수−1)이므로 변환 키에 넣는다.
- **순위**: "3위"·"세 번째"·"상위 3개국" 같은 서수는 숫자 후보로 뽑되 단위 '위'로 두고, 다른 단위와 섞이지 않게 한다.

### Gaps
- 한국어 숫자 표기(만·억·조, 천명 표 단위)를 다룬 공개 정규화 라이브러리나 벤치마크는 찾지 못했다.
- Full Fact의 숫자 매칭 내부 방식은 공개 자료 없음.

## 4. 긴 PDF 안에서 쪽 찾기: BM25/임베딩+Rerank, 표 청크에 머리글 붙이기, ColPali/ColQwen 시각 검색

### Takeaway
한국 공공문서 시각 검색 벤치마크(SDS KoPub VDR)에서 **텍스트만 쓴 검색도 R@5 0.68~0.83, 멀티모달 최고는 R@5 0.90**이다. 해커톤 1주에서 ColPali/ColQwen(수 GB 모델, 맥에선 느림)을 붙이는 것보다, 이미 있는 쪽 텍스트로 **BM25(글자 2-gram) 상위 10쪽 → 라이너 /rerank**가 현실적이다. 한국어 시각 검색 모델은 표·구조 문서에 아직 약하다는 것이 두 벤치마크의 공통 결론이다.

### Cited Findings
- **SDS KoPub VDR**(삼성SDS, 한국 공공문서 361개, 40,781쪽, 표·차트·다단, 질의 600개, 공공누리 1유형 256개 포함). 텍스트 검색(PyPDF로 뽑은 글자) R@5: BGE-M3 0.75, Kanana-Nano-2.1B 0.74, Qwen3-Embedding-0.6B 0.68, OpenAI text-embedding-3-large 0.72, Jina-v4 0.79, SDS-7B 0.83. 멀티모달 R@5: DSE-Qwen2-2B 0.46, Nomic-Embed-Multimodal-7B 0.74, Jina-v4 0.74, SDS-7B 0.90. 시각 질의에서 SDS-7B 멀티모달 R@3 0.86 vs 텍스트만 0.67 — [arXiv 2511.04910 v2](https://arxiv.org/html/2511.04910v2)
- **KoViDoRe**: 한국어 시각 문서 검색 벤치마크, 현재 멀티모달 검색 모델들이 특히 구조화된 내용(표 등)과 다양한 질의에 어려움 — [arXiv 2608.20840](https://arxiv.org/pdf/2608.20840). 이전 공개 KoViDoRe 결과표는 ColPali v1.1~1.3만 있고 ColQwen2 행 없음 — [GitHub kovidore-benchmark](https://github.1485827954.workers.dev/whybe-choi/kovidore-benchmark)
- 다국어 VisR-Bench에서 ColQwen2-v0.1이 ColPali-v1.2보다 대부분 언어에서 앞섬(한국어 포함 여부 미확인) — [arXiv 2508.07493](https://arxiv.org/pdf/2508.07493)
- (이전 노트에서 확인) 라이너 `/rerank`(cohere/rerank-v3.5)는 우리 실측에서 한국어 변별력 좋음 — `진행현황.md` 11/12 기록

### Inferences
- 표·차트 쪽은 텍스트 레이어가 비거나 깨져 텍스트 검색이 놓친다(우리 #8·#27 사례와 같은 구조). 해결은 시각 검색 모델보다 **"텍스트가 빈약한 쪽(digit_poor)은 검색 점수와 상관없이 후보에 넣기"** 규칙이 싸다.
- **표 청크 머리글 붙이기**: Rerank에 보낼 쪽 문서를 "표 제목 + 단위 + 열 머리글 + 해당 행" 형태로 만들면, 숫자 칸만 있는 쪽도 핵심어와 겹친다. PyMuPDF `find_tables()`의 `header.names`와 행을 이어 붙이면 된다.
- 동의어·띄어쓰기 문제: BM25를 공백 기준 단어가 아니라 **공백 제거 후 글자 2-gram**으로 하면 '일간 수익률'↔'일간수익률'이 해결되고, 'KOSPI↔코스피' 같은 동의어는 Rerank(의미 기반)가 메운다. 모델이 핵심어 추출 시 "영문·약어·동의어 2~3개"를 같이 내게 하는 것도 싸다.
- ColQwen2를 맥(MPS)에서 돌리는 것은 가능하나 수십 쪽 PDF를 그때그때 색인하면 건당 지연이 커진다(추론, 측정치 없음). 결선 이후 과제로 미룬다.

### Gaps
- ColQwen2/ColPali의 한국어 공공문서 점수(KoPub VDR 표에 없음), 맥에서의 쪽당 색인 시간 수치 없음.
- 라이너 /rerank 입력 길이·문서 수 제한은 문서로 확인 못 함(코드에 `RERANK_DOC_CHARS`로 자르는 중).

## 5. 결과 흔들림 줄이기: temperature 0, 구조화 출력, 다수결, 인용문 원문 대조

### Takeaway
Gemini는 temperature 0·seed 고정에도 같은 입력에 다른 답을 낸다는 보고가 반복된다. 구조화 출력은 형식 흔들림만 없애고 답 내용 흔들림은 못 없앤다. 그래서 **"모델 답 = 후보 제안"으로만 쓰고, 채택은 코드 검증(인용문이 추출 글자/OCR에 실제로 있는지, 숫자가 그 인용문 안에 있는지)**으로 하는 것이 가장 효과적이다. 인용문 매칭은 "정확 일치 → 정규화 일치 → 퍼지(길이 하한)" 계단식이 권장된다.

### Cited Findings
- gemini-2.5-pro가 seed·temperature 고정에도 같은 요청에 다른 출력 — [Google AI 포럼](https://discuss.ai.google.dev/t/the-gemini-api-is-exhibiting-non-deterministic-behavior-for-the-gemini-2-5-pro-model-it-is-producing-different-outputs-for-identical-requests-even-when-a-fixed-seed-is-provided-along-with-a-constant-temperature-this-behavior-has-been-reliably-rep/101331), [포럼: temperature 0에서도 불일치](https://discuss.ai.google.dev/t/inconsistent-gemini-output-with-identical-input-even-at-temperature-0/98096)
- seed는 best-effort, OpenAI 호환 라이브러리로 부르면 seed 미지원이라는 답변 — [Unstract 블로그](https://unstract.com/blog/understanding-why-deterministic-output-from-llms-is-nearly-impossible/), [Google AI 포럼](https://discuss.ai.google.dev/t/consistent-output-gemini-optimize-parametter-for-reproducible/78540) (우리 게이트웨이는 OpenAI 호환 `/chat/completions` 형식)
- 구조화 출력은 겉모양 차이(공백·필드 순서)는 없애지만 답 차이는 못 없앰 — [memx 블로그](https://memx.app/blog/llm-determinism-is-a-lie-29-flapping-cases/) (개인 블로그)
- **인용문 계단식 검증**: 정확 일치 → 정규화 일치 → 임계값 퍼지 일치(퍼지 일치는 '바꿔 쓴 것'으로 표시·추가 확인), 퍼지도 실패면 지어낸 것으로 처리. 퍼지만 쓰면 어휘가 겹치는 엉뚱한 문단에 맞아 버림, 너무 짧은 인용은 판정 생략 — [Vaquill 엔지니어링](https://www.vaquill.ai/engineering/catching-a-fabricated-citation), [Vaquill 4단계 검증](https://www.vaquill.ai/blog/vaquill-4-layer-citation-verification-explained) (법률 AI 회사 글)
- **좌표 대신 인용문으로 근거 위치 찾기**: VLM에게 좌표를 묻는 대신 그대로 옮긴 인용문을 내게 하고 그 인용을 레이아웃 블록에 매칭하면 상자 재현율(IoU 0.5)이 8.1% 이하 → 25.9~46.9%로 오름. 단 캡션 없는 그림은 인용 불가 — [arXiv 2607.24651](https://www.alphaxiv.org/abs/2607.24651)
- 차트 표 추출을 여러 번 뽑아 칸별 중앙값을 내면 최대 23% 상대 향상 — [benchmarklist 요약](https://benchmarklist.com/benchmarks/self_ensembling_vision_language_models_for_chart_data_extraction/)
- RapidFuzz `partial_ratio`는 짧은 문자열이 긴 문자열 안에 부분 일치하는 정도를 재며, `partial_ratio_alignment`는 일치 구간의 시작·끝 위치를 함께 준다 — [RapidFuzz 문서](https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html)

### Inferences
- 인용 검증 순서 제안: ① 공백·줄바꿈 제거 후 정확 포함(지금 `quote_in_pages`) → ② 숫자만 뺀 글자 비교(지금 `quote_letters_on_page`) → ③ `partial_ratio_alignment` ≥ 90 & 인용 길이 ≥ 15자 & **인용 속 숫자가 일치 구간 안 원문 숫자와 정규화 후 같음**. ③에서 위치(alignment)를 얻으면 PyMuPDF `search_for`로 형광펜 좌표도 바로 얻는다.
- 다수결은 비용이 2~3배라 **두 번 답이 다를 때만** 세 번째를 부르는 방식이 낫다(지금 2회 시도 구조에 붙이기 쉬움). 두 답이 같은 쪽·같은 숫자면 확정, 다르면 각 답을 코드 검증해 통과한 것만 남기고, 둘 다 통과하면 Rerank 점수 높은 쪽.
- 프롬프트는 "쪽 번호, 그대로 옮긴 인용문, 숫자, 표라면 행·열 머리글과 단위"를 JSON 필드로 강제 → 코드 검증할 재료가 늘어난다. 잘라 보낸 PDF는 프롬프트에 "1쪽=원본 p37" 대응을 넣지 말고 코드에서만 바꾼다(모델 혼동 방지, 추론).

### Gaps
- 라이너 게이트웨이가 `temperature`·`seed`·`response_format`(JSON 스키마)을 Gemini에 넘기는지 문서로 확인 못 함 → 같은 요청 5회 반복 실측 필요.
- 숫자 쪽 찾기 과제에서 self-consistency 효과를 잰 연구는 찾지 못했다.

## 6. 큰 PDF(20MB 초과): 쪽 나누기, 목차 먼저 읽기

### Takeaway
28MB PDF는 대개 고해상도 이미지 때문이다. 글자 검색은 원본에서 로컬로 하고(크기 무관), 게이트웨이에는 **후보 쪽만 잘라 이미지 압축까지 한 PDF**를 보낸다. 목차는 PyMuPDF `get_toc()`(책갈피)가 있으면 즉시 쓰고, 없으면 앞 3~5쪽 글자에서 "제목……쪽번호"를 정규식으로 읽어 장 단위 후보를 줄인다.

### Cited Findings
- Gemini 자체 PDF 한도 50MB·1,000쪽(이전 보고서에서 확인), 라이너 게이트웨이 base64 요청당 20MB(`진행현황.md` 기록) — [Gemini 문서 처리](https://aistudio.google.com/docs/document-processing)
- Docling 논문: PDF 글자 파싱만은 M3 Max 쪽당 44ms → 수백 쪽 PDF도 텍스트 전처리 비용은 작다 — [arXiv 2501.17887](https://arxiv.org/pdf/2501.17887)

### Inferences (PyMuPDF 1.27 기능 기반, 측정 전)
- 자른 PDF 크기 줄이기: `doc.select([...])` 또는 `insert_pdf` 후 `save(garbage=4, deflate=True)`, 그래도 크면 PyMuPDF `Document.rewrite_images(dpi_threshold=..., dpi_target=150, quality=75)`로 이미지 재압축(1.24 이후 기능으로 알고 있음, 우리 버전에서 동작 확인 필요). qpdf·pypdf 추가 설치는 불필요.
- 목차: `doc.get_toc()`가 비어 있지 않으면 장 제목과 핵심어를 Rerank에 넣어 상위 장의 쪽 범위만 숫자 검색. 국책연구원 보고서는 대부분 책갈피가 있다(추론, 우리 PDF로 확인 필요).
- 속도: 쪽별 글자 추출은 PDF 해시 기준 캐시(JSON)로 저장하면 재평가·시연 때 다시 안 뽑는다. pdftotext와 PyMuPDF를 둘 다 돌리지 말고 PyMuPDF 먼저, 숫자 깨짐 쪽만 `pdftotext -layout`.

### Gaps
- 라이너 게이트웨이의 실제 요청 크기·쪽수 한도, 큰 요청에서의 지연 시간 공식 수치 없음.
- 28MB PDF가 이미지 때문인지 글꼴 내장 때문인지 아직 확인 안 함(`pdfimages -list`로 바로 확인 가능).

---

### 우선순위 제안 (위 근거를 종합한 추론, 남은 기간 기준)
1. **(반나절) 코드 검증 강화**: 인용 계단식 검증 + 표시 자릿수 반올림 일치 + %·%p·배 규칙 + 정수 환산 인덱스. 흔들림과 표 숫자 문제를 같이 줄임.
2. **(반나절) 쪽 후보 고르기**: 공백 제거 2-gram BM25 상위 10쪽 + 숫자 빈약 쪽 강제 포함 → /rerank 상위 3~5쪽만 게이트웨이. 표 쪽은 "표 제목+단위+머리글+행" 문자열로 Rerank.
3. **(하루) 그림 글자 쪽**: `rawdict`로 감지 → 그림 영역만 dpi 300 잘라 ocrmac + 같은 영역 PNG를 게이트웨이에 함께. 답 숫자는 OCR 글자에 있어야 채택.
4. **(선택, 반나절) Upstage Document Parse Enhanced**를 3번도 실패한 쪽에만(쪽당 약 3센트). 한국어 표 성능은 우리 실패 5건으로 먼저 시험.
5. ColPali/ColQwen·Docling 전체 변환은 이번 마감에는 넣지 않음.
