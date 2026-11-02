import { h, renderReport, renderCandidates } from "/web/report-view.js";
import { discover, renderDiscover } from "/web/discover.js";

const $ = (sel) => document.querySelector(sel);
let current = null;

const dateFmt = (t) => new Date(t * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

function setStatus(text, error = false) {
  const el = $("#status");
  el.className = "status" + (error ? " error" : "");
  el.textContent = text;
  el.hidden = !text;
}

function show(report) {
  current = report.uid || null;
  $("#out").replaceChildren(renderReport(report));
  document.querySelectorAll(".history li").forEach((li) => li.classList.toggle("active", li.dataset.uid === current));
  $(".main").scrollTop = 0;
}

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
        h("b", {}, it.title || it.query), h("small", {}, [(it.issns || [])[0], dateFmt(it.created_at)].filter(Boolean).join(" · "))),
      h("button", { class: "del", type: "button", title: "Delete report", "aria-label": "Delete report", onclick: () => remove(it.uid) }, "×"));
    return li;
  }));
}

async function openSaved(uid) {
  const res = await fetch(`/api/history/${uid}`);
  if (!res.ok) { refreshHistory(); return; }
  setStatus("");
  show(await res.json());
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
  const LABELS = { scopus: "Scopus", crest: "CREST", sanlic: "SANLiC", kanalregister: "Kanalregister", journal_site: "Journal website" };
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
    $("#out").replaceChildren(renderDiscover(result, (rec) => {
      $("#q").value = rec.issns[0] || rec.title;
      run({ q: rec.issns[0] || rec.title });
    }));
  } catch (err) {
    setStatus("Search failed: " + err.message, true);
  } finally {
    $("#fgo").disabled = false;
  }
});

window.journalScope = { refreshHistory };
if (new URLSearchParams(location.search).has("desktop")) $("#desk").hidden = false;
refreshHistory();
refreshStatus();
setInterval(refreshStatus, 15000);
