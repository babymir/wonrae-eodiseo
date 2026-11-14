"""논문을 인용한 기사 문장 → 원 논문(라이너 Scholar 검색 우선, PubMed 초록 보조)에서 숫자 대조(11/14).

연구진·학술지 결과를 전하는 기사 숫자는 대개 논문 초록·본문에 나온다.
1) 라이너 Scholar 검색(영어 검색어): 원 논문을 1위로, 전문 PDF 링크까지 주는 경우가 많다(11/14 시험).
   결과 요약(초록 앞부분)에 기사 숫자가 있으면 그 요약도 따로 후보로 둔다(본문 PDF 를 못 받을 때 대비).
2) PubMed(미국 국립의학도서관, 키 없음): 의학 논문 초록. PubMed 웹 페이지는 자동 접속에 빈 화면이라 초록 조회(efetch)를 받는다.
- 검색: esearch(원어 검색어) → 논문 번호 상위 MAX_PAPERS 개 → esummary 로 제목
- 후보: 문서 주소 = efetch 초록(XML, 일반경로.fetch_doc 이 글자로 읽음), 사람에게 보여 줄 주소 = PubMed 논문 페이지
"""
import hashlib
import json
import re
import urllib.parse

import 일반경로
from liner_api import scholar_search
from 기관별 import Candidate, fetch_bytes
from 숫자표기 import to_regex, variants

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
TOOL = "originfinder"      # NCBI 권장 tool 이름(개인정보·이메일은 보내지 않음)
MAX_PAPERS = 4
RESEARCH = re.compile(r"연구진|연구팀|논문|학술지|저널|메타분석|게재|코호트|임상시험|바이오뱅크|Journal|Lancet|JAMA|BMJ|Nature|Science")


def is_research(sentence: str) -> bool:
    return bool(RESEARCH.search(sentence))


def _get(path: str, **params) -> bytes:
    params.update(tool=TOOL)
    return fetch_bytes(EUTILS + path + "?" + urllib.parse.urlencode(params))


MAX_SCHOLAR = 4


def _key(url: str) -> str:
    return "gen_" + hashlib.sha1(url.encode()).hexdigest()[:12]


def scholar(info: dict) -> list[Candidate]:
    """라이너 Scholar 검색 후보(영어 검색어). 요약에 기사 숫자가 있으면 그 요약 후보를 앞에."""
    q = info.get("원어검색어", "").strip()
    if not q:
        return []
    try:
        rs = scholar_search(q, max_results=MAX_SCHOLAR * 2)
    except RuntimeError as e:  # Scholar 실패는 PubMed 로 계속(이유 출력)
        print(f"    라이너 Scholar 검색 실패 '{q}': {e}")
        return []
    print(f"    라이너 Scholar 검색 '{q}' → {len(rs)}건")
    pats = [to_regex(v.text) for v in variants(info.get("숫자", ""))]
    snips, docs = [], []
    for r in rs[:MAX_SCHOLAR]:
        url, desc = r.get("url") or "", str(r.get("description") or "")
        if not url:
            continue
        title = f"{r.get('title', '')[:100]} ({r.get('journal') or '논문'})"
        c = Candidate(title, url, url, _key(url))
        docs.append(c)
        if desc:
            일반경로.register_snippet(url, desc)
            if any(p.search(일반경로.normalize_numbers(desc)) for p in pats):
                snips.append(Candidate(title, url, url, "gen_snip_" + c.file_key[4:]))
    return snips + docs


def search(info: dict) -> list[Candidate]:
    """라이너 Scholar 후보 + PubMed 초록 후보."""
    return scholar(info) + pubmed(info)


def pubmed(info: dict) -> list[Candidate]:
    """원어 검색어(영어)로 PubMed 논문 후보. 검색어가 없으면 빈 목록."""
    q = info.get("원어검색어", "").strip()
    if not q:
        return []
    try:
        ids = json.loads(_get("esearch.fcgi", db="pubmed", retmode="json", retmax=MAX_PAPERS, term=q))
        ids = ids.get("esearchresult", {}).get("idlist", [])
        if not ids:
            print(f"    PubMed 검색 '{q}' → 0건")
            return []
        summary = json.loads(_get("esummary.fcgi", db="pubmed", retmode="json", id=",".join(ids))).get("result", {})
    except (RuntimeError, ValueError) as e:  # PubMed 실패는 다른 경로로 계속(이유 출력)
        print(f"    PubMed 검색 실패 '{q}': {e}")
        return []
    print(f"    PubMed 검색 '{q}' → {len(ids)}건")
    out = []
    for pmid in ids:
        title = (summary.get(pmid) or {}).get("title") or f"PubMed {pmid}"
        page = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        abstract = EUTILS + "efetch.fcgi?" + urllib.parse.urlencode({"db": "pubmed", "retmode": "xml", "tool": TOOL, "id": pmid})
        out.append(Candidate(title, page, abstract, "gen_" + hashlib.sha1(abstract.encode()).hexdigest()[:12]))
    return out
