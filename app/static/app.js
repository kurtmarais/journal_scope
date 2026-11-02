import { h, renderReport, renderCandidates } from "/web/report-view.js";

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
  setStatus("Checking SCImago, CREST, Kanalregister, SANLiC and the journal website. This usually takes 15-60 seconds.");
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

window.journalScope = { refreshHistory };
if (new URLSearchParams(location.search).has("desktop")) $("#desk").hidden = false;
refreshHistory();
refreshStatus();
setInterval(refreshStatus, 15000);
