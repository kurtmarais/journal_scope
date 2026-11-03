import { findSaved, h, missingSections, renderCandidates, renderReport, renderSaved } from "/web/report-view.js";
import { discover, recordToReport, renderDiscover } from "/web/discover.js";

const $ = (sel) => document.querySelector(sel);
let current = null;

const dateFmt = (t) => new Date(t * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

function setStatus(text, error = false) {
  const el = $("#status");
  el.className = "status" + (error ? " error" : "");
  el.textContent = text;
  el.hidden = !text;
}

let saved = [];
let shownReport = null;

function show(report, extra = null) {
  current = report.uid || null;
  shownReport = report;
  const entry = findSaved(saved, report);
  $("#out").replaceChildren(renderReport(report, { saved: !!entry, onStar: () => toggleStar(report), extra }));
  document.querySelectorAll(".history li").forEach((li) => li.classList.toggle("active", li.dataset.uid === current));
  $("#savedBtn").classList.remove("active");
  $(".main").scrollTop = 0;
}

async function loadSaved() {
  try { saved = await (await fetch("/api/saved")).json(); } catch { return; }
  $("#savedCount").textContent = saved.length ? `(${saved.length})` : "";
}

async function toggleStar(report) {
  const entry = findSaved(saved, report);
  if (entry) await fetch(`/api/saved/${encodeURIComponent(entry.uid)}`, { method: "DELETE" });
  else await fetch("/api/saved", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(report) });
  await loadSaved();
  if (shownReport === report) show(report, savedExtra(report));
}

function savedExtra(report) {
  const issn = ((report.identity || {}).issns || [])[0];
  const title = (report.identity || {}).title || report.query;
  return h("button", { type: "button", class: "secondary-btn recheck", onclick: () => { $("#q").value = issn || title; run({ q: issn || title }); } },
    "Check again (refresh this report)");
}

async function showSaved() {
  await loadSaved();
  setStatus("");
  current = null;
  shownReport = null;
  document.querySelectorAll(".history li").forEach((li) => li.classList.remove("active"));
  $("#savedBtn").classList.add("active");
  $("#out").replaceChildren(renderSaved(saved,
    (item) => show(item.report, savedExtra(item.report)),
    async (item) => { await fetch(`/api/saved/${encodeURIComponent(item.uid)}`, { method: "DELETE" }); showSaved(); }));
  $(".main").scrollTop = 0;
}
$("#savedBtn").addEventListener("click", showSaved);

async function refreshHistory() {
  let items = [];
  try { items = await (await fetch("/api/history")).json(); } catch { return; }
  const list = $("#history");
  if (!items.length) {
    list.replaceChildren(h("li", { class: "empty" }, "Reports you check appear here."));
    return;
  }
  list.replaceChildren(...items.map((it) => {
    const li = h("li", { "data-uid": it.uid, class: it.uid === current ? "active" : "" },
      h("button", { class: "open", type: "button", title: it.title, onclick: () => openSaved(it.uid) },
        h("b", {}, it.title || it.query), h("small", {}, [(it.issns || [])[0], dateFmt(it.created_at)].filter(Boolean).join(" · "),
          it.outdated ? h("span", { class: "old", title: "Made before the newest sections were added" }, " · older") : null)),
      h("button", { class: "del", type: "button", title: "Delete report", "aria-label": "Delete report", onclick: () => remove(it.uid) }, "×"));
    return li;
  }));
}

async function openSaved(uid) {
  const res = await fetch(`/api/history/${uid}`);
  if (!res.ok) { refreshHistory(); return; }
  setStatus("");
  const report = await res.json();
  show(report, historyExtra(report));
}

// ---------------------------------------------------- updating older reports
function historyExtra(report) {
  const missing = missingSections(report);
  const btn = h("button", { type: "button", class: "secondary-btn recheck" },
    missing.length ? "Update this report" : "Check again (update this report)");
  btn.addEventListener("click", () => updateOne(report.uid, btn));
  return h("div", { class: "outdated-box" }, btn,
    missing.length ? h("p", {}, `Made before ${missing.join(", ")} ${missing.length > 1 ? "were" : "was"} added. Updating runs the check again and replaces this report.`) : null);
}

async function updateOne(uid, btn) {
  btn.disabled = true;
  btn.textContent = "Updating…";
  setStatus("Updating the report…");
  const poll = setInterval(async () => {
    try {
      const p = await (await fetch("/api/progress")).json();
      if (p.report) $("#out").replaceChildren(renderReport(p.report));
    } catch { /* ignore */ }
  }, 900);
  try {
    const res = await fetch(`/api/history/${uid}/refresh`, { method: "POST" });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
    const report = await res.json();
    setStatus("");
    await loadSaved();
    show(report, historyExtra(report));
    refreshHistory();
    pollBulk();
  } catch (err) {
    setStatus("Update failed: " + err.message, true);
    openSaved(uid);
  } finally {
    clearInterval(poll);
  }
}

let bulkTimer = null;
function renderBulk(st) {
  const box = $("#bulk");
  if (st.running) {
    box.hidden = false;
    box.replaceChildren(...[h("button", { type: "button", disabled: true }, `Updating ${Math.min(st.done + 1, st.total)} of ${st.total}…`),
      st.current ? h("p", {}, st.current) : null].filter(Boolean));
    clearTimeout(bulkTimer);
    bulkTimer = setTimeout(pollBulk, 2000);
    return;
  }
  const failed = (st.failed || []).length;
  if (!st.outdated && !failed) { box.hidden = true; box.replaceChildren(); return; }
  box.hidden = false;
  const btn = h("button", { type: "button", title: "Run older reports again so they include the newest sections. Each keeps its place in History." },
    `Update older reports (${st.outdated})`);
  btn.addEventListener("click", async () => {
    if (!confirm(`Run ${st.outdated} older report${st.outdated > 1 ? "s" : ""} again so they include the newest sections? This takes about a minute each and runs in the background.`)) return;
    wasRunning = true;
    renderBulk(await (await fetch("/api/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })).json());
  });
  box.replaceChildren(...[st.outdated ? btn : null,
    failed ? h("p", { title: st.failed.map((f) => `${f.title}: ${f.error}`).join("\n") },
      `${failed} could not be updated and kept their old report (hover for why).`) : null].filter(Boolean));
}

let wasRunning = false;
async function pollBulk() {
  let st;
  try { st = await (await fetch("/api/refresh")).json(); } catch { return; }
  if (wasRunning && !st.running) { refreshHistory(); loadSaved(); }
  else if (st.running && st.done) refreshHistory();
  wasRunning = st.running;
  renderBulk(st);
}

async function remove(uid) {
  await fetch(`/api/history/${uid}`, { method: "DELETE" });
  if (uid === current) { $("#out").replaceChildren(); current = null; }
  refreshHistory();
}

async function run(params) {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v));
  $("#go").disabled = true;
  $("#out").replaceChildren();
  const LABELS = { scopus: "Scopus", crest: "CREST", sanlic: "SANLiC", kanalregister: "Kanalregister", journal_site: "Journal website", guidelines: "Author guidelines" };
  const STATE = { working: "checking…", found: "done", not_found: "done", skipped: "skipped", error: "failed" };
  const started = Date.now();
  setStatus("Checking Scopus, CREST, SANLiC, Kanalregister and the journal website…");
  let shown = "";
  const poll = setInterval(async () => {
    try {
      const p = await (await fetch("/api/progress")).json();
      const st = p.states || {};
      const parts = Object.keys(LABELS).filter((k) => st[k]).map((k) => `${LABELS[k]}: ${STATE[st[k]] || st[k]}`);
      setStatus(`Checking (${Math.round((Date.now() - started) / 1000)} s)… ` + parts.join(" · "));
      if (p.report) {                       // show finished sources while the rest load
        const key = JSON.stringify(st);
        if (key !== shown) { shown = key; $("#out").replaceChildren(renderReport(p.report)); }
      }
    } catch { /* ignore */ }
  }, 700);
  try {
    const res = await fetch("/api/report?" + qs.toString());
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
    const report = await res.json();
    setStatus("");
    if ((report.candidates || []).length) {
      $("#out").replaceChildren(renderCandidates(report.query, report.candidates,
        (c) => run({ q: report.query, sid: c.source_id, site: $("#site").value.trim() })));
    } else {
      await loadSaved();
      show(report);
      refreshHistory();
    }
  } catch (err) {
    setStatus("Lookup failed: " + err.message, true);
  } finally {
    clearInterval(poll);
    $("#go").disabled = false;
  }
}

async function refreshStatus() {
  try {
    const s = await (await fetch("/api/status")).json();
    $("#sync").textContent = [s.sync, s.version && `v${s.version}`].filter(Boolean).join(" · ");
  } catch { /* server stopping */ }
}

$("#search").addEventListener("submit", (e) => {
  e.preventDefault();
  run({ q: $("#q").value.trim(), site: $("#site").value.trim() });
});

// ---------------------------------------------------------- find journals
async function getJSON(url, options) {
  const res = await fetch(url, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

const provider = {
  openalex: (mode, q) => getJSON(`/api/discover/openalex?${new URLSearchParams({ mode, q })}`),
  doaj: (mode, q) => getJSON(`/api/discover/doaj?${new URLSearchParams({ mode, q })}`),
  scopus: (q) => getJSON(`/api/discover/scopus?${new URLSearchParams({ mode: "scope", q })}`),
  crestSearch: (q) => getJSON(`/api/discover/crest?${new URLSearchParams({ mode: "title", q })}`),
  refmatch: (issns, titles) => getJSON("/api/refmatch", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ issns, titles }) }),
};

$("#find").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#fq").value.trim();
  const mode = $("#fmode").value;
  $("#fgo").disabled = true;
  $("#out").replaceChildren();
  setStatus(mode === "scope" ? "Finding journals that publish on this topic…" : "Finding journals with these words in their titles…");
  try {
    const result = await discover(mode, q, provider);
    setStatus("");
    const asReport = (rec) => ({ identity: { issns: rec.issns, title: rec.title } });
    $("#out").replaceChildren(renderDiscover(result, (rec) => {
      $("#q").value = rec.issns[0] || rec.title;
      run({ q: rec.issns[0] || rec.title });
    }, {}, {
      isSaved: (rec) => !!findSaved(saved, asReport(rec)),
      onStar: async (rec) => {
        const entry = findSaved(saved, asReport(rec));
        if (entry) await fetch(`/api/saved/${encodeURIComponent(entry.uid)}`, { method: "DELETE" });
        else await fetch("/api/saved", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(recordToReport(rec)) });
        await loadSaved();
      },
    }));
  } catch (err) {
    setStatus("Search failed: " + err.message, true);
  } finally {
    $("#fgo").disabled = false;
  }
});

window.journalScope = { refreshHistory: () => { refreshHistory(); loadSaved(); pollBulk(); } };
if (new URLSearchParams(location.search).has("desktop")) $("#desk").hidden = false;
refreshHistory();
pollBulk();
loadSaved();
refreshStatus();
setInterval(refreshStatus, 15000);
