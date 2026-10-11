"use strict";
/* 振り返りビュー(フレームワークなし)。利用者の入力はすべて textContent で入れる(innerHTMLに入れない)。 */

const $ = (id) => document.getElementById(id);
const SIDE = { b: "先手", w: "後手" };
const FEEL = { regret: "悔しい", unsure: "迷った", ok: "問題なし" };
const KIND = { long: "長考", low_remaining: "残り時間が少ない", swing: "評価値の急落" };
const NS = "http://www.w3.org/2000/svg";

const S = { cfg: null, game: null, cur: 0, marks: new Map(), cands: new Map(), editing: null, runs: [] };

function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) e.append(c);
  return e;
}
function svg(tag, attrs, text) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  if (text != null) e.textContent = text;
  return e;
}

async function api(path, opts = {}) {
  const o = { credentials: "same-origin", headers: {}, ...opts };
  if (o.body && typeof o.body !== "string") { o.body = JSON.stringify(o.body); o.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, o);
  if (r.status === 401 && path !== "/api/login") { show("login"); throw new Error("ログインが必要"); }
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (_) {}
    throw new Error(msg);
  }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r;
}

let flashTimer;
function flash(msg, err) {
  const f = $("flash");
  f.textContent = msg; f.className = err ? "err" : ""; f.hidden = !msg;
  clearTimeout(flashTimer);
  if (msg && !err) flashTimer = setTimeout(() => (f.hidden = true), 4000);
}
const guard = (fn) => async (...a) => { try { await fn(...a); } catch (e) { flash(e.message, true); } };

function show(name) {
  for (const v of ["login", "list", "review", "report"]) $("view-" + v).hidden = v !== name;
  $("user-area").hidden = name === "login";
  $("sheet").hidden = true;
  window.scrollTo(0, 0);
}

/* ---------- ルーティング ---------- */
async function route() {
  flash("");
  const m = location.hash.match(/^#\/g\/([^/]+)(\/report)?$/);
  try {
    if (!S.cfg) S.cfg = await api("/api/config");
    await api("/api/me");
  } catch (_) { show("login"); return; }
  if (!m) return showList();
  if (m[2]) return showReport(m[1]);
  return openReview(m[1]);
}
window.addEventListener("hashchange", guard(route));

/* ---------- ログイン ---------- */
$("login-form").addEventListener("submit", guard(async (ev) => {
  ev.preventDefault();
  await api("/api/login", { method: "POST", body: { email: $("login-email").value, password: $("login-pass").value } });
  $("login-pass").value = "";
  location.hash = "#/";
  await route();
}));
$("btn-logout").addEventListener("click", guard(async () => { await api("/api/logout", { method: "POST" }); show("login"); }));

/* ---------- 一覧・読み込み ---------- */
async function showList() {
  const items = await api("/api/games");
  const ul = $("game-list");
  ul.replaceChildren(...items.map((g) => {
    const res = g.result ? `${g.result.n}手目 ${g.result.word}` : "結果なし";
    return h("li", {}, h("button", { class: "item", onclick: () => (location.hash = "#/g/" + g.id) },
      g.title, g.sample ? h("span", { class: "badge" }, "サンプル") : null,
      h("br"), h("small", { class: "note" }, `${g.totalMoves}手 / ${res} / 自分: ${SIDE[g.mySide]}`)));
  }));
  if (!items.length) ul.replaceChildren(h("li", { class: "note" }, "対局がない。下から棋譜を読み込む。"));
  show("list");
}

$("kif-file").addEventListener("change", guard(async () => {
  const f = $("kif-file").files[0];
  if (!f) return;
  const buf = await f.arrayBuffer();
  let text;
  try { text = new TextDecoder("utf-8", { fatal: true }).decode(buf); }
  catch (_) { text = new TextDecoder("shift_jis").decode(buf); }
  $("kif-text").value = text.replace(/^﻿/, "");
}));
$("paste-form").addEventListener("submit", guard(async (ev) => {
  ev.preventDefault();
  const body = { kif: $("kif-text").value, title: $("kif-title").value || null, mySide: $("my-side").value || null };
  const g = await api("/api/games", { method: "POST", body });
  $("kif-text").value = ""; $("kif-title").value = "";
  location.hash = "#/g/" + g.id;
}));

/* ---------- 振り返りビュー ---------- */
async function openReview(id) {
  const [game, marks, runs] = await Promise.all([
    api("/api/games/" + id), api(`/api/games/${id}/marks`), api(`/api/games/${id}/runs`)]);
  S.game = game; S.cur = 0; S.runs = runs;
  S.marks = new Map(marks.map((m) => [m.moveNo, m]));
  S.cands = new Map();
  const q = [...runs].reverse().find((r) => r.kind === "questions");
  if (q) loadCands(q);
  $("game-title").textContent = game.title;
  const m = game.meta, d = game.derived;
  const lim = d.limitSec ? `持ち時間 ${d.limitSec / 60}分切れ負け` : "持ち時間 不明";
  $("game-info").textContent = `${game.totalMoves}手 / ${game.result ? game.result.n + "手目 " + game.result.word : "結果なし"} / 自分: ${SIDE[game.mySide]} / ${lim}` +
    (d.evalAvailable ? "" : " / 評価値なし");
  const w = $("game-warn");
  w.hidden = !game.warnings.length;
  w.textContent = game.warnings.join(" / ");
  $("st-range").max = game.totalMoves;
  $("st-range").value = 0;
  buildMoveList();
  show("review");
  if (!game.positionsAvailable) flash("局面を再現できない手がある。盤面は途中まで。手順・時間グラフ・印は使える。", true);
  if (!q) guard(runSuggest)();
  const start = S.pending || 0; S.pending = 0;
  goto(start);
}

async function runSuggest() {
  const run = await api(`/api/games/${S.game.id}/agent/questions`, { method: "POST" });
  S.runs.push(run);
  loadCands(run);
  renderLists(); drawGraph(); markMoveList();
  if (run.output.degraded) flash("候補印は、エージェントを使わず決まった規則だけで選んだ。");
}
function loadCands(run) {
  S.cands = new Map((run.output.marks || []).map((m) => [m.n, m]));
}

function goto(n) {
  const max = S.game.totalMoves;
  S.cur = Math.max(0, Math.min(max, n));
  const img = $("board");
  const pos = Math.min(S.cur, S.game.positionCount - 1);
  img.src = `/api/games/${S.game.id}/board/${pos}.svg?perspective=${S.game.mySide}`;
  for (const k of [pos - 1, pos + 1]) if (k >= 0 && k < S.game.positionCount) new Image().src = `/api/games/${S.game.id}/board/${k}.svg?perspective=${S.game.mySide}`;
  $("st-label").textContent = `${S.cur}手目`;
  $("st-range").value = S.cur;
  const mv = S.cur ? S.game.moves[S.cur - 1] : null;
  const info = $("move-info");
  if (!mv) info.textContent = "開始局面";
  else {
    const parts = [`${mv.n}手目 ${SIDE[mv.side]}${mv.side === S.game.mySide ? "(自分)" : ""} ${mv.text}`];
    if (mv.spentSec != null) parts.push(`消費 ${mv.spentSec}秒`);
    if (mv.remainingSec != null) parts.push(`残り ${mv.remainingSec}秒`);
    info.textContent = parts.join(" / ");
    const c = S.cands.get(mv.n);
    if (c) info.append(h("br"), h("span", { class: "note" }, "候補印: " + c.reason));
  }
  const btn = $("btn-mark");
  const has = S.cur > 0 && S.marks.has(S.cur);
  btn.disabled = S.cur === 0;
  btn.classList.toggle("has", has);
  btn.textContent = S.cur === 0 ? "手を進めてから印を付ける" : has ? "印を編集" : "ここが悔しかった!";
  drawGraph();
  for (const li of $("move-list").children) li.classList.toggle("cur", Number(li.dataset.n) === S.cur);
  const ol = $("move-list"), cur = ol.querySelector("li.cur");
  if (cur) ol.scrollTop = Math.max(0, cur.offsetTop - ol.offsetTop - ol.clientHeight / 2);  // ページ全体は動かさない
}

for (const [id, f] of [["st-first", () => 0], ["st-prev", () => S.cur - 1], ["st-next", () => S.cur + 1], ["st-last", () => S.game.totalMoves]])
  $(id).addEventListener("click", () => goto(f()));
$("st-range").addEventListener("input", (e) => goto(Number(e.target.value)));
document.addEventListener("keydown", (e) => {
  if ($("view-review").hidden || !$("sheet").hidden || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) && document.activeElement.type !== "range") return;
  if (e.key === "ArrowLeft") goto(S.cur - 1);
  else if (e.key === "ArrowRight") goto(S.cur + 1);
  else if (e.key === "Home") goto(0);
  else if (e.key === "End") goto(S.game.totalMoves);
});

function buildMoveList() {
  const ol = $("move-list");
  ol.replaceChildren(...S.game.moves.map((m) =>
    h("li", { "data-n": m.n }, h("button", { onclick: () => goto(m.n) },
      `${m.n} ${m.side === "b" ? "☗" : "☖"}${m.text}`))));
  markMoveList();
}
function markMoveList() {
  for (const li of $("move-list").children) {
    const n = Number(li.dataset.n);
    li.classList.toggle("marked", S.marks.has(n));
    li.classList.toggle("candm", !S.marks.has(n) && S.cands.has(n));
  }
  renderLists();
}

function renderLists() {
  const cl = $("cand-list");
  const cands = [...S.cands.values()];
  cl.replaceChildren(...(cands.length ? cands.map((c) =>
    h("li", { class: "cand-item" }, h("button", { class: "item", onclick: () => goto(c.n) },
      `${c.n}手目 `, h("small", {}, c.source === "rule" ? "(規則)" : "(エージェント)"), h("br"),
      h("small", {}, c.reason)))) : [h("li", { class: "note" }, "候補はまだない(または該当なし)")]));
  const ml = $("mark-list");
  const marks = [...S.marks.values()].sort((a, b) => a.moveNo - b.moveNo);
  ml.replaceChildren(...(marks.length ? marks.map((m) =>
    h("li", { class: "mark-item" }, h("button", { class: "item", onclick: () => goto(m.moveNo) },
      `${m.moveNo}手目 ${S.game.moves[m.moveNo - 1].text} — ${FEEL[m.feeling]}`, h("br"),
      h("small", { class: "note" }, [...m.causeTags, m.note].filter(Boolean).join(" / ") || "(原因・メモなし)")))) :
    [h("li", { class: "note" }, "まだ印がない。盤面を見て「ここが悔しかった!」を押す")]));
}

/* ---------- 時間グラフ ---------- */
function drawGraph() {
  const g = S.game, el = $("time-graph");
  const W = 360, H = 120, top = 8, base = H - 14, plotH = base - top;
  const N = g.moves.length, bw = W / N;
  const mx = Math.max(1, ...g.moves.map((m) => m.spentSec || 0));
  const parts = [];
  g.moves.forEach((m, i) => {
    const hh = plotH * (m.spentSec || 0) / mx;
    parts.push(svg("rect", { x: i * bw, y: base - hh, width: Math.max(bw - 0.5, 0.5), height: hh,
      fill: m.side === g.mySide ? "var(--accent)" : "var(--opp)" }));
  });
  const th = g.derived.longThreshold;
  if (th && th <= mx) {
    parts.push(svg("line", { x1: 0, x2: W, y1: base - plotH * th / mx, y2: base - plotH * th / mx, stroke: "#c0392b", "stroke-dasharray": "3 3", "stroke-width": 1 }));
    parts.push(svg("text", { x: W - 2, y: base - plotH * th / mx - 2, "text-anchor": "end", "font-size": 8, fill: "#c0392b" }, `長考の目安 ${Math.round(th)}秒`));
  }
  const lim = g.derived.limitSec;
  if (lim) {
    const pts = g.moves.filter((m) => m.side === g.mySide && m.remainingSec != null)
      .map((m) => `${(m.n - 0.5) * bw},${base - plotH * Math.max(0, m.remainingSec) / lim}`);
    if (pts.length) parts.push(svg("polyline", { points: pts.join(" "), fill: "none", stroke: "#2a8a4a", "stroke-width": 1.5 }));
  }
  parts.push(svg("line", { x1: 0, x2: W, y1: base, y2: base, stroke: "var(--line)" }));
  for (const c of S.cands.keys()) if (!S.marks.has(c))
    parts.push(svg("circle", { cx: (c - 0.5) * bw, cy: 5, r: 3.5, fill: "none", stroke: "var(--cand)", "stroke-dasharray": "2 1.5", "stroke-width": 1.2 }));
  for (const n of S.marks.keys())
    parts.push(svg("circle", { cx: (n - 0.5) * bw, cy: 5, r: 4, fill: "var(--mark)" }));
  if (S.cur > 0) parts.push(svg("rect", { x: (S.cur - 1) * bw - 0.5, y: 0, width: Math.max(bw, 1.5) + 1, height: base, fill: "none", stroke: "#f3a712", "stroke-width": 1.5 }));
  parts.push(svg("text", { x: 2, y: H - 3, "font-size": 8, fill: "var(--muted)" }, "1手目"));
  parts.push(svg("text", { x: W - 2, y: H - 3, "font-size": 8, "text-anchor": "end", fill: "var(--muted)" }, `${N}手目  最大${mx}秒`));
  el.replaceChildren(...parts);
}
$("time-graph").addEventListener("click", (e) => {
  const r = e.currentTarget.getBoundingClientRect();
  const x = (e.clientX - r.left) / r.width;
  goto(Math.floor(x * S.game.moves.length) + 1);
});

/* ---------- 印 ---------- */
$("btn-mark").addEventListener("click", () => { if (S.cur > 0) openSheet(S.cur); });

function openSheet(n) {
  S.editing = n;
  const m = S.marks.get(n);
  const mv = S.game.moves[n - 1];
  $("sheet-title").textContent = `${n}手目 ${SIDE[mv.side]} ${mv.text}`;
  for (const r of document.querySelectorAll('input[name="feeling"]')) r.checked = r.value === (m ? m.feeling : "regret");
  $("tag-box").replaceChildren(...S.cfg.causeTags.map((t) =>
    h("label", {}, h("input", { type: "checkbox", value: t, checked: m && m.causeTags.includes(t) ? "checked" : false }), t)));
  for (const c of $("tag-box").querySelectorAll("input")) c.checked = !!(m && m.causeTags.includes(c.value));
  $("mark-note").value = m ? m.note : "";
  $("sheet-del").hidden = !m;
  $("sheet").hidden = false;
  $("sheet").scrollTop = 0;
}
$("sheet-close").addEventListener("click", () => ($("sheet").hidden = true));
$("sheet-form").addEventListener("submit", guard(async (ev) => {
  ev.preventDefault();
  const n = S.editing;
  const body = {
    feeling: document.querySelector('input[name="feeling"]:checked').value,
    causeTags: [...$("tag-box").querySelectorAll("input:checked")].map((c) => c.value),
    note: $("mark-note").value,
  };
  const m = await api(`/api/games/${S.game.id}/marks/${n}`, { method: "PUT", body });
  S.marks.set(n, m);
  $("sheet").hidden = true;
  flash(`${n}手目に印を付けた`);
  markMoveList(); goto(S.cur);
}));
$("sheet-del").addEventListener("click", guard(async () => {
  const n = S.editing;
  await api(`/api/games/${S.game.id}/marks/${n}`, { method: "DELETE" });
  S.marks.delete(n);
  $("sheet").hidden = true;
  flash(`${n}手目の印を取り消した`);
  markMoveList(); goto(S.cur);
}));

$("btn-back").addEventListener("click", () => (location.hash = "#/"));
$("btn-delete-game").addEventListener("click", guard(async () => {
  if (!confirm("この対局と印・実行記録を削除する。元に戻せない。")) return;
  await api("/api/games/" + S.game.id, { method: "DELETE" });
  location.hash = "#/";
}));
$("btn-report").addEventListener("click", guard(async () => {
  $("btn-report").disabled = true; flash("レポートを作成中…");
  try { await api(`/api/games/${S.game.id}/agent/report`, { method: "POST" }); }
  finally { $("btn-report").disabled = false; }
  location.hash = `#/g/${S.game.id}/report`;
}));

/* ---------- レポート ---------- */
async function showReport(id) {
  if (!S.game || S.game.id !== id) S.game = await api("/api/games/" + id);
  const [rep, runs] = await Promise.all([api(`/api/games/${id}/report`).catch(() => null), api(`/api/games/${id}/runs`)]);
  const body = $("report-body");
  $("btn-back-review").onclick = () => (location.hash = "#/g/" + id);
  if (!rep) {
    body.replaceChildren(h("p", {}, "レポートがまだない。"), h("button", { class: "primary", onclick: guard(async () => {
      await api(`/api/games/${id}/agent/report`, { method: "POST" }); await showReport(id); }) }, "レポートを作る"));
    show("report"); return;
  }
  const o = rep.output, cmp = o.compare, nar = o.narration;
  const jump = (n) => h("a", { href: "#/g/" + id, onclick: () => { S.pending = n; } }, `${n}手目`);
  const sec = (title, ...kids) => h("section", { class: "report-sec" }, h("h3", {}, title), ...kids);
  const list = (xs, fmt) => xs.length ? h("ul", {}, xs.map((x) => h("li", {}, fmt(x)))) : h("p", { class: "note" }, "なし");
  const kids = [];
  if (o.noMarks) kids.push(h("p", { class: "warn" }, "本人の印がない。時間のデータだけを示している。振り返りビューで印を付けると、突き合わせができる。"));
  if (o.degraded) kids.push(h("p", { class: "warn" }, "AIの出力を使えなかったため、決まった規則だけで作った。"));
  kids.push(sec("所見 ", h("span", { class: nar.source === "ai" ? "tag-ai" : "tag-rule" }, nar.source === "ai" ? "AI所感" : "規則による要約"),
    h("p", {}, nar.summary),
    nar.items.length ? h("ul", {}, nar.items.map((i) => h("li", {}, `${i.n}手目: ${i.comment}`))) : null,
    nar.nextFocus.length ? h("div", {}, h("strong", {}, "次に意識すること"), h("ul", {}, nar.nextFocus.map((f) => h("li", {}, f)))) : null));
  kids.push(sec("感覚とデータのずれ(データ)",
    h("h4", {}, "一致(印とデータが重なった)"), list(cmp.coincide, (x) => [x.n + "手目 ", `${FEEL[x.feeling]} / ${x.dataKinds.join("・")}`]),
    h("h4", {}, "感覚だけ(印はあるが、データでは目立たない)"), list(cmp.feelingOnly, (x) => `${x.n}手目 ${FEEL[x.feeling]}`),
    h("h4", {}, "データだけ(自分の手で時間を使ったが、印がない)"), list(cmp.dataOnly, (x) => `${x.n}手目 ${x.dataKinds.join("・")}${x.spentSec != null ? ` ${x.spentSec}秒` : ""}`),
    h("h4", {}, "時間の癖"), list(cmp.timeHabit, (x) => `${x.n}手目 ${x.causeTags.join("・")} / ${x.dataKinds.join("・")}`)));
  kids.push(sec(`考える問題(${o.problems.length}問)`,
    h("p", { class: "note" }, "最善手は未取得。正解を示す問題ではない。自分の見立てを書き、あとで別の手段で確かめる。"),
    ...o.problems.map((p, i) => h("div", { class: "problem" },
      h("strong", {}, `問題${i + 1}: ${p.n}手目の直前 / 手番 ${SIDE[p.toMove]} / ${p.origin === "user_mark" ? "本人の印" : "候補(本人の印ではない)"}`),
      h("img", { src: `/api/games/${id}/board/${p.n - 1}.svg?perspective=${S.game.mySide}`, alt: `${p.n}手目の直前の局面` }),
      h("span", {}, p.question + "?"),
      h("small", { class: "note" }, `実戦: ${p.played}${p.nextMove ? " → 次の手 " + p.nextMove : ""} / 最善手: 未取得`)))));
  if (o.cards.length) kids.push(sec("参照先(プロ棋士の解説。内容は元の動画で確認する)",
    ...o.cards.map((c) => h("p", {}, `${c.proName}「${c.videoTitle}」`, h("br"), h("a", { href: c.url, target: "_blank", rel: "noopener noreferrer" }, c.url), h("br"), h("small", {}, c.summaryOwnWords || "")))));
  kids.push(sec("実行記録(エージェントが何を呼び、何を弾いたか)",
    ...runs.map((r) => h("div", {},
      h("strong", {}, `${r.kind === "questions" ? "実行1: 候補印の選定" : "実行2: 突き合わせと教材化"}`),
      h("p", { class: "note" }, `LLM呼び出し ${r.guardrail.llmCalls}回 / 不採用 ${r.guardrail.rejected}回 / 縮退運転 ${r.guardrail.degraded ? "あり" : "なし"}` +
        (r.guardrail.reasons.length ? ` / 理由: ${r.guardrail.reasons.join(" | ")}` : "")),
      h("table", { class: "steps" }, h("tr", {}, h("th", {}, "ツール"), h("th", {}, "引数"), h("th", {}, "結果"), h("th", {}, "ms")),
        r.steps.map((s) => h("tr", {}, h("td", {}, s.tool), h("td", {}, s.args), h("td", {}, s.resultSummary), h("td", {}, String(s.ms)))))))));
  kids.push(h("p", { class: "note" }, o.disclaimer));
  kids.push(h("p", {}, h("a", { class: "btnlike", href: `/api/games/${id}/report.pdf` }, "PDFをダウンロード")));
  body.replaceChildren(...kids);
  show("report");
}

/* ---------- 起動 ---------- */
route().catch((e) => flash(e.message, true));
