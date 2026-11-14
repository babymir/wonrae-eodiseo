// 「원래 어디서?」 화면 동작: 문장 보내기 → 진행 단계 보여 주기 → 결과(기사 ↔ 원문 쪽) 그리기
// 보안: HTML 문자열을 쓰지 않고 요소를 직접 만든다(글자는 모두 텍스트 노드).
const $ = (id) => document.getElementById(id);
const POLL_MS = 1200;

// group: 맨 위 3단 묶음(원문 확인됨 / 사람 확인 필요 / 원문 못 찾음) — 판정 9종을 비전문가가 바로 읽게(11/15)
const VERDICT = {
  "일치": { cls: "v-same", group: "원문 확인됨", head: "원문과 같습니다" },
  "표현 바뀜": { cls: "v-shift", group: "원문 확인됨", head: "원문과 표현이 달라졌습니다" },
  "숫자 다름": { cls: "v-shift", group: "원문 확인됨", head: "원문의 숫자와 다릅니다" },
  "기사가 계산": { cls: "v-calc", group: "원문 확인됨", head: "기사가 원문 숫자로 계산한 값입니다" },
  "표현 다름 후보": { cls: "v-shift", group: "사람 확인 필요", head: "같은 숫자가 원문에 있지만 표현이 다릅니다" },
  "통계표·원자료 인용": { cls: "v-table", group: "사람 확인 필요", head: "보고서가 아니라 통계표·원자료에서 온 숫자입니다" },
  "원문에 없음": { cls: "v-none", group: "원문 못 찾음", head: "기사가 밝힌 자료에서 이 숫자를 찾지 못했습니다" },
  "원출처 미확인": { cls: "v-none", group: "원문 못 찾음", head: "원문을 아직 찾지 못했습니다 — 확인할 곳을 알려 드립니다" },
  "확인 대상 아님": { cls: "v-none", group: "확인 대상 아님", head: "통계 원문과 대조할 문장이 아닙니다" },
};
// 공개 문서 원문이 없는 종류(시세·업계 자료)는 '못 찾음'이 아니라 '원문이 문서로 없음'으로 보여 준다
const NO_PUBLIC_DOC = { "시세·조회 화면 자료": "시세·조회 화면에만 있는 숫자입니다 — 확인할 화면을 알려 드립니다",
  "업계·관계자 자료": "업계·관계자가 기자에게 준 숫자로 보입니다 — 공개 문서 원문이 없을 수 있습니다" };
// 원문을 못 찾았을 때 제목: 실제로 보여 줄 것이 있는 만큼만 말한다(11/16: 안내가 없는데 '확인할 곳을 알려 드립니다'로 나옴)
function noneHead(r, kind) {
  if (r["시간초과"]) return "시간 안에 확인을 마치지 못했습니다 — 지금까지 찾은 후보를 보여 드립니다";
  if (NO_PUBLIC_DOC[kind]) return NO_PUBLIC_DOC[kind];
  if (r["출처종류"] === "의원실 자료") return "국회의원실이 받은 자료로 보입니다 — 공개 원문이 없을 수 있습니다";
  if ((r["안내"] || {})["원문위치"]?.length) return "원문이 있을 곳은 찾았지만 숫자는 확인하지 못했습니다 — 아래 위치에서 직접 확인해 주세요";
  if (r["안내"]) return "원문을 찾지 못했습니다 — 확인할 곳을 알려 드립니다";
  return "원문을 찾지 못했습니다";
}
// 칸별 비교(2단 판정): 판정 모델이 칸마다 답한 것
const AXIS_NAMES = { "지표": "지표", "대상": "대상", "시점": "시점", "비교기준": "비교 기준", "수준변화": "수준·변화량" };

let timer = null;
let lastRequest = null;     // '그래도 확인하기'에 다시 쓸 요청
let shownPartial = false;   // 교차 확인 전 임시 판정을 이미 그렸는지

// 요소 만들기: h("p", {class: "x"}, "글자", 다른요소, ...)
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

function link(href, text) {
  return h("a", { href, target: "_blank", rel: "noopener" }, text);
}

// 숫자에 형광펜: 글자 사이 공백·쉼표 차이를 허용해 찾고, 찾은 부분만 <mark> 로
function marked(text, numbers) {
  const s = String(text ?? "");
  const pats = [...new Set(numbers.filter(Boolean).map((n) => String(n).replace(/\s+/g, "").replace(/(명|원|건|가구|곳)$/, "")))]
    .filter(Boolean)
    .sort((a, b) => b.length - a.length)  // 긴 표기 먼저(3.0% 보다 13.0% 같은 것)
    .map((core) => core.split("").map((ch) => ch.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("[\\s,]*"));
  if (!pats.length) return [s];
  const hits = [...s.matchAll(new RegExp(`(?<![\\d.])(?:${pats.join("|")})`, "g"))];
  if (!hits.length) return [s];
  const parts = []; let last = 0;
  for (const m of hits) {
    parts.push(s.slice(last, m.index), h("mark", {}, m[0]));
    last = m.index + m[0].length;
  }
  parts.push(s.slice(last));
  return parts;
}

// 각주용 주소: 게시판 검색에 쓰인 값(ref_bid·tag·nPage 등)을 빼서 짧게
function shortUrl(u) {
  try {
    const url = new URL(u);
    ["ref_bid", "tag", "nPage", "keyField", "keyWord", "mainXml"].forEach((k) => url.searchParams.delete(k));
    return url.toString();
  } catch { return u; }
}

function cleanTitle(t) {
  return String(t || "").split(/\s*\|\s*/)[0].replace(/\s+/g, " ").replace(/^보 도 자 료$/, "보도자료")
    .replace(/([가-힣])\d$/, "$1")  // PDF 표지 제목 끝에 붙은 각주 번호("정책 과제1")
    .trim();
}

// PDF 글자 추출 찌꺼기 정리: 굵은 글씨·표 칸이 빠지며 남는 ", , ," 같은 쉼표 덩어리
function tidy(text) {
  return String(text || "").replace(/\(\s*\)/g, " ").replace(/(^|\s)\*+(?=\s)/g, " ").replace(/(\s*,){2,}\s*/g, " ").replace(/\s+([,.])/g, "$1").replace(/\s{2,}/g, " ").trim();
}

function fileOf(path) { return String(path || "").split("/").pop(); }
// 일반 경로에서 원문이 PDF 가 아니라 웹 페이지(글자만 저장한 .txt)인 경우: 쪽 그림 대신 인용 + 원문 페이지 링크
function isWeb(path) { return String(path || "").endsWith(".txt"); }

// 진행 기록을 사람 말로
function friendly(line) {
  if (line.startsWith("[0] 기사 주소")) return line.slice(4);
  if (line.includes("다시 대조")) return "처음 고른 숫자를 못 찾아, 같은 문장의 다른 숫자로 다시 대조했습니다";
  if (line.includes("안내: ")) return "원문을 찾지 못한 이유와 확인할 곳을 찾았습니다";
  let m0 = line.match(/라이너 검색 '(.+)' \(일반 경로[^)]*\) → (\d+)건/);
  if (m0) return `기관 누리집 검색: ${m0[1].replace(/filetype:pdf/, "PDF만")} — ${m0[2]}건`;
  if (line.startsWith("[1]")) return "기사 문장을 기관·보고서·숫자·기준 시점으로 나눴습니다 (라이너 모델)";
  let m = line.match(/라이너 검색 '(.+)' → (\d+)건/);
  if (m) return `라이너 검색: ${m[1].replace(/filetype:pdf/, "PDF만")} — ${m[2]}건`;
  m = line.match(/원 보고서 후보 (\d+)건/);
  if (m) return `원 보고서 후보 ${m[1]}건을 모았습니다`;
  m = line.match(/후보: (.+?) — .+?, (\d+)쪽, 기준시점/);
  if (m) return `후보 보고서를 받았습니다: ${cleanTitle(m[1])} (${m[2]}쪽)`;
  m = line.match(/\[3\] (.+?): 숫자 후보 (\d+)곳/);
  if (m) return `원문에서 같은 숫자 후보 ${m[2]}곳을 찾았습니다`;
  if (line.startsWith("[4]")) return "글자로 못 찾아 PDF를 직접 읽게 했습니다 (라이너 게이트웨이 모델)";
  m = line.match(/(\d+)쪽 중 (\d+)쪽만 잘라 보냄/);
  if (m) return `${m[1]}쪽 중 관련 있는 ${m[2]}쪽만 보냈습니다`;
  if (line.includes("PDF 전체")) return "PDF 전체를 보냈습니다";
  if (line.includes("누리집이 요청을 잠시 막음")) return "기관 누리집이 잠시 막아 기다렸다 다시 받습니다";
  if (line.startsWith("[0] 입구 필터")) return "확인할 통계 숫자 문장인지 먼저 살폈습니다";
  m = line.match(/최신 목록 후보: (.+?) \(Rerank/);
  if (m) return `기관 최신 목록에서 후보를 찾았습니다: ${m[1]} (라이너 Rerank)`;
  if (line.includes("미리 출발")) return "숫자가 글자로 안 잡혀 PDF 직접 읽기를 미리 시작했습니다 (라이너 게이트웨이 모델)";
  if (line.includes("같은 제목의 다른 해 판")) return "같은 이름의 해마다 판 중 기사 날짜 기준 최신 판을 골랐습니다";
  m = line.match(/계산 찾기: .+ 식 (\d+)개/);
  if (m) return `원문 숫자 조합을 코드로 계산해 같은 값이 나오는 식 ${m[1]}개를 찾았습니다`;
  if (line.includes("규칙 확인:")) return "단위·증감 방향·비교 기간을 규칙으로 대조했습니다";
  if (line.includes("Decisions 교차 확인")) return "두 번째 판정 모델로 기간·대상·단위·지표를 따로 확인했습니다 (라이너 Decisions)";
  return null;
}

function showSteps(lines) {
  const items = [...new Set(lines.map(friendly).filter(Boolean))];
  $("steps").replaceChildren(...items.map((s) => h("li", {}, s)));
  return items;
}

// 진행 중 단계별 결과 표(나오는 대로 칸을 채움). 쪽 그림은 판정보다 먼저 보여 준다
function showStages(events) {
  const rows = [];
  for (const ev of events || []) {
    const d = ev["내용"];
    if (ev["종류"] === "분해") {
      rows.push(row("나눈 문장", `${d["기관"] || "기관 모름"} · 숫자 ${d["숫자"]}${d["기준시점"] ? " · 기준 시점 " + d["기준시점"] : ""}`));
    } else if (ev["종류"] === "후보") {
      rows.push(row("원 보고서 후보", h("ul", { class: "plain" }, d.slice(0, 4).map((t) => h("li", {}, cleanTitle(t))))));
    } else if (ev["종류"] === "보고서") {
      rows.push(row("고른 보고서", cleanTitle(d)));
    } else if (ev["종류"] === "쪽" && isWeb(d["PDF"])) {
      rows.push(row("숫자를 찾은 곳", h("p", {}, "기관 누리집 웹 페이지")));
    } else if (ev["종류"] === "쪽") {
      const q = new URLSearchParams({ file: d["PDF"], page: d["쪽"], mark: d["표기"], crop: "1" });
      const img = h("img", { alt: `원문 ${d["쪽"]}쪽`, src: `/api/page?${q}` });
      img.addEventListener("error", () => img.remove());
      rows.push(row("숫자를 찾은 쪽", h("p", {}, `PDF ${d["쪽"]}쪽`), h("figure", { class: "page" }, img)));
    } else if (ev["종류"] === "판정") {
      rows.push(row("판정", `${d["판정"]} — 두 번째 판정 모델로 확인하는 중`));
    }
  }
  $("stages").replaceChildren(...rows);
  $("stages-table").hidden = !rows.length;
}

async function start(e, force = false, prefer = null) {
  e?.preventDefault?.();
  const sentence = $("sentence").value.trim();
  if (!sentence) { $("sentence").focus(); return; }
  $("error").hidden = true; $("result").hidden = true; shownPartial = false;
  $("go").disabled = true; $("progress").hidden = false; $("steps").replaceChildren(); showStages([]);
  const began = Date.now();
  clearInterval(timer);
  $("elapsed").textContent = "0초";
  timer = setInterval(() => { $("elapsed").textContent = `${Math.round((Date.now() - began) / 1000)}초`; }, 500);
  try {
    const res = await fetch("/api/check", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sentence, url: $("url").value.trim(), context: $("context").value, date: $("date").value, force, prefer }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "요청을 받지 못했습니다.");
    poll(data.job, began);
  } catch (err) { fail(err.message); }
}

async function poll(id, began) {
  try {
    const res = await fetch(`/api/job?id=${encodeURIComponent(id)}`);
    const job = await res.json();
    if (!res.ok) throw new Error(job.error);
    const steps = showSteps(job.steps || []);
    showStages(job.events);
    if (job.status === "running") {
      if (job.partial && !shownPartial) {   // 판정은 먼저 보여 주고, 교차 확인은 끝나면 채운다
        shownPartial = true;
        render(job.partial, steps, Math.round((Date.now() - began) / 1000), true);
      }
      setTimeout(() => poll(id, began), POLL_MS); return;
    }
    clearInterval(timer); $("go").disabled = false; $("progress").hidden = true;
    if (job.status === "error") throw new Error(`원문을 찾다가 멈췄습니다. ${job.error}`);
    render(job.result, steps, Math.round((Date.now() - began) / 1000), false);
  } catch (err) { fail(err.message); }
}

function fail(msg) {
  clearInterval(timer); $("go").disabled = false; $("progress").hidden = true;
  $("error").textContent = msg; $("error").hidden = false;
}

function summaryText(r) {
  return {
    "일치": "기사 숫자가 원문에 같은 뜻으로 나옵니다.",
    "표현 바뀜": "숫자는 원문에 있지만, 아래 '달라진 부분'의 뜻이 바뀌었습니다.",
    "기사가 계산": "원문에 이 숫자가 그대로 나오지는 않고, 원문 숫자로 계산하면 나옵니다.",
    "표현 다름 후보": r["이유"] || "원문에 같은 숫자가 있지만 기사와 표현이 다릅니다. 같은 내용인지 직접 확인해 주세요.",
    "숫자 다름": `원문에는 같은 지표가 ${r["원문숫자"] || ""}(으)로 나옵니다. 같은 대상·시점의 숫자인지 아래 원문을 직접 확인하세요.`,
    "통계표·원자료 인용": "보도자료 PDF에는 없습니다. 기사가 국가통계포털(KOSIS) 통계표나 마이크로데이터를 직접 인용했습니다.",
    // 찾아본 범위를 밝힌다: 못 찾았다는 것이지 기사가 틀렸다는 뜻이 아니다(팩트체크 기관의 '확인 불가' 구분)
    "원문에 없음": `확인한 보고서 ${(r["확인한문서"] || []).length || 1}건에서 같은 숫자를 찾지 못했고, 원문 숫자로 계산되지도 않습니다. ` +
      "기사가 틀렸다는 뜻은 아닙니다. 다른 판이나 다른 자료에서 온 숫자일 수 있습니다.",
    "원출처 미확인": r["이유"] || "기관 누리집에서 원 보고서 PDF를 찾지 못했습니다.",
    "확인 대상 아님": r["이유"] || "통계 결과를 전하는 문장이 아니라 원문과 대조하지 않았습니다.",
  }[r["판정"]] || "";
}

// 원문 칸에 보여 줄 쪽·인용·형광펜 숫자: 보통은 찾은 쪽, '기사가 계산'이면 재료 숫자가 있는 쪽
function sourceView(r, info) {
  if (r["쪽"] !== null && r["쪽"] !== undefined) {
    return { page: r["쪽"], quotes: [r["원문인용"]], marks: [r["찾은표기"] || r["원문숫자"], info["숫자"]], note: "" };
  }
  const calc = r["계산확인"] || {};
  if (r["판정"] === "기사가 계산" && calc["쪽"]) {
    const quotes = [...new Set((calc["재료"] || []).map((m) => m["원문"]).filter(Boolean))];
    return { page: calc["쪽"], quotes, marks: (calc["재료"] || []).map((m) => m["숫자"]), note: ", 재료 숫자" };
  }
  return null;
}

// 대조표 '원문' 칸 내용: 인용 문장 + 형광펜 친 쪽 그림 + 링크
function sourceCell(r, view, pdf) {
  const side = h("div", { class: "cell" });
  if (view && isWeb(pdf)) {
    view.quotes.forEach((t) => side.append(h("blockquote", { class: "quote" }, marked(tidy(t), view.marks))));
    side.append(h("p", {}, link(r["상세페이지"], "기관 누리집 원문 페이지 열기")));
  } else if (view && pdf) {
    view.quotes.forEach((t) => side.append(h("blockquote", { class: "quote" }, marked(tidy(t), view.marks))));
    if (r["표문맥"]) side.append(h("p", { class: "muted" }, "표 위치: ", marked(tidy(r["표문맥"]), view.marks)));
    const q = new URLSearchParams({ file: pdf, page: view.page, mark: view.marks.filter(Boolean).join("|"),
      quote: view.quotes[0] || "", crop: "1" });
    const img = h("img", { alt: `원문 ${view.page}쪽. 숫자에 형광펜을 칠했습니다`, src: `/api/page?${q}` });
    const toggle = h("button", { type: "button", class: "linkish" }, "쪽 전체 보기");
    toggle.addEventListener("click", () => {
      const whole = q.get("crop") === "1";
      q.set("crop", whole ? "0" : "1"); img.src = `/api/page?${q}`;
      toggle.textContent = whole ? "표시한 부분만 보기" : "쪽 전체 보기";
    });
    img.addEventListener("error", () => img.remove());
    side.append(h("figure", { class: "page" }, img, h("figcaption", {}, toggle,
      link(`/api/pdf?file=${encodeURIComponent(pdf)}#page=${view.page}`, "원문 PDF 열기"),
      r["상세페이지"] ? link(r["상세페이지"], "기관 누리집에서 보기") : null)));
  } else {
    side.append(h("p", { class: "empty" }, "원문에서 이 숫자가 나온 곳을 찾지 못했습니다."));
    if (r["상세페이지"]) {
      side.append(h("p", {}, link(r["상세페이지"], "기관 누리집에서 원문 보기"),
        pdf ? [" / ", link(`/api/pdf?file=${encodeURIComponent(pdf)}`, "PDF 열기")] : null));
    }
  }
  return side;
}

// 표 한 줄: [머리, 내용]
function row(label, ...content) {
  return h("tr", {}, h("th", { scope: "row" }, label), h("td", {}, ...content));
}


function render(r, steps, secs, partial) {
  const gate = r["입구"] || {};
  const v = gate["통과"] === false && gate["종류"] === "지원 범위 밖"
    ? { cls: "v-none", head: "지원 범위 밖 자료입니다" }
    : (VERDICT[r["판정"]] || VERDICT["원출처 미확인"]);
  const info = r["추출"] || {};
  const found = r["쪽"] !== null && r["쪽"] !== undefined;
  // 원출처 미확인이면 '찾아본 문서'는 원 보고서가 아니다(11/14: 미확인인데 원 보고서 칸에 보도자료 제목이 나옴)
  const title = r["판정"] === "원출처 미확인" ? "" : cleanTitle(r["보고서"]);
  const pdf = fileOf(r["PDF"]);
  const box = $("result");
  const parts = [];

  const view = sourceView(r, info);
  const calcOk = r["판정"] === "기사가 계산";
  const checks = {
    "기사가 계산": [["원 보고서", !!title], ["재료 숫자", !!view], ["계산", calcOk]],
    "통계표·원자료 인용": [["보도자료 확인", !!title], ["보도자료에 숫자 없음", true], ["통계표 후보", !!r["통계표후보"]?.length]],
    "숫자 다름": [["원 보고서", !!title], ["원문 쪽", found], ["같은 지표", true], ["숫자 같음", false]],
  }[r["판정"]] || [["원 보고서", !!title], ["원문 쪽", found], ["숫자", found], ["뜻", r["판정"] === "일치"]];

  // 확인 결과 표: 판정 / 원 보고서 / 확인한 것
  const kind = (r["안내"] || {})["유형"];
  const headText = r["판정"] === "원출처 미확인" ? noneHead(r, kind) : v.head;
  const spotOnly = r["판정"] === "원출처 미확인" && !r["시간초과"] && !!(r["안내"] || {})["원문위치"]?.length;
  const head = h("strong", { class: `verdict-text ${v.cls}`, tabindex: "-1" },
    v.group ? `[${spotOnly ? "원문 위치만 찾음" : v.group}] ` : "", headText);
  const ok = /^(\d)\/\1$/.test(String(r["기준시점확인"] || ""));  // "2/2" 처럼 다 맞았을 때만
  const source = title ? [`${info["기관"] || ""} 「${title}」`,
    view ? (isWeb(pdf) ? ", 웹 페이지" : `, PDF ${view.page}쪽${view.note}`) : "",
    ok && info["기준시점"] ? ` (기준 시점 ${info["기준시점"]})` : ""].join("")
    : spotOnly ? "원문이 있을 곳만 찾음(아래 '확인할 곳')" : "찾지 못함";
  const calc = r["계산확인"] || {};
  const rows = [
    row("판정", head, h("p", { class: "verdict-sub" }, summaryText(r))),
    row("원 보고서", source, r["제목확인"] && title ? h("p", { class: "warn" }, r["제목확인"]) : null,
      r["지원밖기관"] && title && !["기관 모름", "학술 논문"].includes(r["지원밖기관"]) ? h("p", { class: "muted" }, `'${r["지원밖기관"]}' 공식 누리집과 공공기관 문서에서 찾아 대조했습니다.`) : null),
    row("확인한 것", h("ul", { class: "checks" },
      checks.map(([t, yes]) => h("li", { class: yes ? "yes" : "no" }, t, h("span", { class: "ox", "aria-label": yes ? "확인함" : "확인 못 함" }, yes ? "○" : "–"))))),
  ];
  if (gate["통과"] === false && !partial) {
    const again = h("button", { type: "button" }, "그래도 확인하기");
    again.addEventListener("click", () => start(null, true));
    rows.push(row("다시 확인", h("p", { class: "muted" }, "통계 숫자 문장이 맞다면 그대로 확인할 수 있습니다. "), again));
  }
  const cross = r["교차확인"] || {};
  if (partial) {
    rows.push(row("교차 확인", h("p", { class: "muted" }, "두 번째 판정 모델(라이너 Decisions)로 확인하는 중입니다…")));
  } else if (cross["판정"] === "숫자 다름") {
    rows.push(row("교차 확인", h("p", { class: "muted" },
      `두 번째 판정 모델(라이너 Decisions)에 '기사 숫자와 원문 숫자가 같은 지표·대상·시점인가'를 따로 물었고, 같은 지표로 봤습니다(확률 ${Math.round((cross["확신도"] || 0) * 100)}%).`)));
  } else if (cross["판정"]) {
    const conf = Math.round((cross["확신도"] || 0) * 100);
    const axes = cross["다른축"]?.length ? ` 기간·대상·단위·지표를 따로 물었을 때 '${cross["다른축"].join("·")}'이(가) 다를 수 있다고 봤습니다.` : "";
    rows.push(row("교차 확인", cross["사람확인권장"]
      ? h("p", { class: "warn" }, `두 번째 판정 모델(라이너 Decisions)은 '${cross["판정"]}'(확신도 ${conf}%)으로 봤습니다.${axes} 원문을 직접 확인하길 권합니다.`)
      : h("p", { class: "muted" }, `두 번째 판정 모델(라이너 Decisions)도 '${cross["판정"]}'으로 봤습니다. 확신도 ${conf}%. 기간·대상·단위·지표도 따로 물어 모두 같다고 봤습니다.`)));
  }
  if (calcOk) {
    rows.push(row("계산식", h("p", { class: "formula" }, String(calc["식"] || "").replace(/ - /g, " − ")),
      h("p", { class: "calc-note" }, `원문에는 이 값이 직접 나오지 않으니, 인용할 때는 계산했다는 점을 밝히는 편이 안전합니다.`),
      calc["후보수"] ? h("p", { class: "muted" }, `찾은 방법: 코드가 원문 숫자 조합을 모두 계산해 같은 값이 나오는 식 ${calc["후보수"]}개를 찾고, 라이너 모델이 같은 뜻의 식을 골랐습니다.`) : null));
  }
  if (r["다른숫자로확인"]) {
    const o = r["다른숫자로확인"];
    rows.push(row("확인한 숫자", h("p", { class: "muted" }, `처음 고른 ${o["처음고른숫자"]}은(는) 원문에서 찾지 못해, 같은 문장의 ${o["확인한숫자"]}로 원문을 확인했습니다.`)));
  }
  if (r["숫자별확인"]?.length) {  // 문장 속 숫자 하나하나가 원문에 있는지
    rows.push(row("문장 속 숫자", h("ul", { class: "plain" }, r["숫자별확인"].map((x) =>
      h("li", {}, `${x["숫자"]} — `, x["쪽"] ? (isWeb(pdf) ? "원문에 있음" : `원문 ${x["쪽"]}쪽에 있음`) : h("strong", { class: "warn" }, "이 문서에서 못 찾음"))))));
  }
  if (r["안내"]) {  // 원문을 못 찾았을 때 왜 없는지·어디서 확인할지
    const g = r["안내"];
    rows.push(row("확인할 곳", h("p", {}, `${g["유형"]}: ${g["설명"]}`),
      g["링크"]?.length ? h("ul", { class: "plain" }, g["링크"].map((t) => h("li", {}, link(t["주소"], t["제목"])))) : null));
  }
  if (r["통계표후보"]?.length) {
    rows.push(row("통계표 후보", h("ul", { class: "plain" }, r["통계표후보"].map((t) => h("li", {}, link(t["주소"], t["제목"]))))));
  }
  if (r["확인못한부분"]) rows.push(row("확인 못 한 부분", h("p", { class: "muted" }, r["확인못한부분"])));
  if (r["참고값"]) {  // 같은 지표로 보이지만 다른 자료일 수 있는 원문 값(판정에는 쓰지 않음)
    const ref = r["참고값"];
    rows.push(row("참고", h("p", { class: "muted" }, ref["안내"]),
      h("blockquote", { class: "quote" }, `${ref["쪽"] ? ref["쪽"] + "쪽: " : ""}${tidy(ref["원문인용"] || "")}`)));
  }
  if (r["판정"] === "원문에 없음" && r["확인한문서"]?.length) {
    rows.push(row("찾아본 보고서", h("ul", { class: "plain" }, r["확인한문서"].map((t) => h("li", {}, cleanTitle(t))))));
  }
  if (r["후보"]?.length) {
    const none = r["판정"] === "원출처 미확인";   // 못 찾았을 때 후보는 원문이 아님을 분명히
    rows.push(row("찾아본 곳", none ? h("p", { class: "muted" }, "원문으로 확인되지 않은 문서입니다(참고용).") : null,
      h("ul", { class: "plain" }, r["후보"].map((t) => h("li", {}, link(t["주소"], t["제목"]))))));
  }
  if (r["사람이고른보고서"]) rows.splice(2, 0, row("고른 보고서", h("p", { class: "muted" }, `직접 고른 「${cleanTitle(r["사람이고른보고서"])}」로 다시 확인한 결과입니다.`)));
  parts.push(h("table", { class: "summary" }, h("caption", {}, "확인 결과"), h("tbody", {}, rows)));

  // 다른 후보 보고서: 자동으로 고른 보고서가 틀렸을 수 있을 때 사람이 골라 다시 확인(확신이 낮으면 펼쳐 둠)
  const alts = r["다른후보"] || [];
  if (alts.length && !partial) {
    const altRows = alts.map((a) => {
      // 다시 확인은 세 기관 PDF만 서버가 받는다(일반 경로 후보 gen_ 은 링크만)
      const again = String(a["키"] || "").startsWith("gen_") ? "–" : h("button", { type: "button" }, "이 보고서로 다시 확인");
      if (again !== "–") again.addEventListener("click", () => start(null, false, { "제목": a["제목"], "상세페이지": a["상세페이지"], "PDF주소": a["PDF주소"], "키": a["키"] }));
      return h("tr", {},
        h("td", { "data-label": "보고서" }, a["상세페이지"] ? link(a["상세페이지"], cleanTitle(a["제목"])) : cleanTitle(a["제목"])),
        h("td", { "data-label": "같은 숫자" }, a["숫자쪽"] ? `${a["숫자쪽"]}쪽에 있음` : "–"),
        h("td", { "data-label": "다시 확인" }, again));
    });
    const lead = r["확신낮음"]
      ? "자동으로 고른 보고서가 맞지 않을 수 있습니다. 아래 후보 중 기사가 인용한 보고서를 골라 다시 확인해 보세요."
      : "자동으로 고른 보고서 말고도 이런 후보가 있었습니다.";
    const box = h("details", { class: "alts" }, h("summary", {}, `다른 후보 보고서 ${alts.length}건`),
      h("p", { class: r["확신낮음"] ? "warn" : "muted" }, lead),
      h("table", { class: "daejo" }, h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "보고서"),
        h("th", { scope: "col" }, "같은 숫자"), h("th", { scope: "col" }, "다시 확인"))), h("tbody", {}, altRows)));
    if (r["확신낮음"]) box.setAttribute("open", "");
    parts.push(box);
  }

  // 대조표: 기사 | 원문
  parts.push(h("table", { class: "daejo" }, h("caption", {}, "대조표"),
    h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "기사"),
      h("th", { scope: "col" }, view ? `원문 (${view.page}쪽${view.note})` : "원문"))),
    h("tbody", {}, h("tr", {},
      h("td", { "data-label": "기사" }, h("p", { class: "article-text" }, marked(r["입력"], [info["숫자"]]))),
      h("td", { "data-label": view ? `원문 (${view.page}쪽)` : "원문" }, sourceCell(r, view, pdf))))));

  // 칸별 비교: 판정 모델이 지표·대상·시점·비교 기준·수준을 따로 본 결과(판정은 코드가 이 칸들로 정함)
  const axes = r["칸별비교"] || {};
  const axisRows = Object.keys(AXIS_NAMES).filter((k) => axes[k] && axes[k]["판단"] && axes[k]["판단"] !== "알수없음");
  if (axisRows.length) {
    parts.push(h("table", { class: "daejo diffs" }, h("caption", {}, "칸별 비교 (AI가 해석한 내용 — 직접 확인하세요)"),
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "칸"), h("th", { scope: "col" }, "기사"),
        h("th", { scope: "col" }, "원문"), h("th", { scope: "col" }, "판단"))),
      h("tbody", {}, axisRows.map((k) => h("tr", {},
        h("td", { "data-label": "칸", class: "kind" }, AXIS_NAMES[k]),
        h("td", { "data-label": "기사" }, axes[k]["기사"] || "–"),
        h("td", { "data-label": "원문", class: "src" }, axes[k]["원문"] || "–"),
        h("td", { "data-label": "판단" }, axes[k]["판단"] === "다름" ? h("span", { class: "changed" }, "다름") : axes[k]["판단"]))))));
  }

  const diffs = r["차이"] || [];
  if (diffs.length) {
    parts.push(h("table", { class: "daejo diffs" }, h("caption", {}, "달라진 부분"),
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "구분"), h("th", { scope: "col" }, "기사"), h("th", { scope: "col" }, "원문"))),
      h("tbody", {}, diffs.map((d) => h("tr", {},
        h("td", { "data-label": "구분", class: "kind" }, d["항목"]),
        h("td", { "data-label": "기사" }, h("span", { class: "changed" }, d["기사"])),
        h("td", { "data-label": "원문", class: "src" }, h("span", { class: "changed" }, d["원문"]),
          d["근거"] ? h("p", { class: "muted" }, d["근거"]) : null))))));
  }
  if (view) {
    const cite = `${info["기관"] || ""}, 「${title}」, ${view.page}쪽.${r["상세페이지"] ? " " + shortUrl(r["상세페이지"]) : ""}`;
    const done = h("span", { class: "done", hidden: "" });
    const copy = async (text, msg) => {
      try { await navigator.clipboard.writeText(text); done.textContent = msg; }
      catch { done.textContent = "복사하지 못했습니다. 아래 글을 직접 선택해 복사하세요."; }
      done.hidden = false;
    };
    const b1 = h("button", { type: "button" }, "출처 각주 복사");
    const b2 = h("button", { type: "button" }, "원문 문장 복사");
    b1.addEventListener("click", () => copy(cite, "각주를 복사했습니다"));
    b2.addEventListener("click", () => copy(view.quotes.map(tidy).join("\n"), "원문 문장을 복사했습니다"));
    parts.push(h("div", { class: "actions" }, b1, b2, done), h("p", { class: "cite" }, cite));
  }
  const t = r["시간"] || {};
  const timing = t["단계초"] ? h("p", { class: "muted" }, "단계별 시간: " +
    Object.entries(t["단계초"]).filter(([, v]) => v >= 0.1).map(([k, v]) => `${k} ${v.toFixed(1)}초`).join(", ")) : null;
  // 이번 확인에 쓴 라이너 API(종류별 횟수)
  const apiName = (c) => c["무엇"] === "검색" ? "Search API" : c["무엇"] === "Rerank" ? "Rerank API"
    : /Decisions/.test(c["무엇"]) ? "Decisions API" : /PDF|그림/.test(c["무엇"]) ? "Model API(게이트웨이, PDF 읽기)" : "Model API";
  const apiCount = {};
  (t["라이너호출"] || []).forEach((c) => { apiCount[apiName(c)] = (apiCount[apiName(c)] || 0) + 1; });
  const apis = Object.keys(apiCount).length ? h("p", { class: "muted" }, "이번 확인에 쓴 라이너 API: " +
    Object.entries(apiCount).map(([k, v]) => `${k} ${v}회`).join(", ")) : null;
  parts.push(h("details", { class: "trace" }, h("summary", {}, `어떻게 찾았나 (${secs}초)`),
    h("ol", {}, steps.map((s) => h("li", {}, s)), r["찾은방법"] ? h("li", {}, r["찾은방법"]) : null), apis, timing));

  box.replaceChildren(...parts);
  box.hidden = false;
  head.focus({ preventScroll: true });
  box.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}

$("ask").addEventListener("submit", start);
document.querySelectorAll(".sample").forEach((b) => b.addEventListener("click", () => {
  $("sentence").value = b.dataset.s; $("date").value = b.dataset.d || ""; start();
}));
$("sentence").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) start(e); });
