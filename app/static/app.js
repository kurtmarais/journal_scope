"use strict";

const $ = (sel) => document.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const link = (href, text) => href ? `<a href="${esc(href)}" target="_blank" rel="noopener">${esc(text || href)}</a>` : "";
const kv = (pairs) => {
  const rows = pairs.filter(([, v]) => v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && !v.length));
  return rows.length ? `<dl class="kv">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>` : "";
};
const yesno = (b) => (b === true ? "Yes" : b === false ? "No" : "");

const STATUS_CHIP = {
  found: ["Found", "good"],
  not_found: ["Not found", "warn"],
  error: ["Could not check", "bad"],
  skipped: ["Skipped", "neutral"],
};

function chip(result, override) {
  const [text, tone] = override || STATUS_CHIP[result.status] || [result.status, "neutral"];
  return `<span class="chip ${tone}">${esc(text)}</span>`;
}

function card(title, purpose, result, body, opts = {}) {
  const foot = [
    result.note ? `<p class="note">${esc(result.note)}</p>` : "",
    result.error ? `<p class="err">${esc(result.error)}</p>` : "",
    result.url ? `<p class="src">${link(result.url, "Open on " + (opts.site || "source"))}${result.checked_at ? ` <span class="ctx">checked ${esc(result.checked_at.replace("T", " ").replace("+00:00", " UTC"))}</span>` : ""}</p>` : "",
  ].join("");
  return `<article class="card ${opts.wide ? "wide" : ""}">
    <header><h3>${esc(title)}</h3>${chip(result, opts.chip)}</header>
    <p class="purpose">${esc(purpose)}</p>
    ${body || ""}${foot}
  </article>`;
}

function scimagoCard(r) {
  const d = r.data || {};
  let body = "";
  if (r.status === "found") {
    const sjr = d.sjr ? `${d.sjr.value ?? "-"} ${d.sjr.best_quartile ? `<span class="tag ${tone(d.sjr.best_quartile)}">${esc(d.sjr.best_quartile)}</span>` : ""} <span class="ctx">(${d.sjr.year}, best quartile)</span>` : "";
    const cites = (d.cites_per_doc_2y || []).map((c) => `${c.year}: ${c.value}`).join(" &middot; ");
    const areas = (d.subject_areas || []).map((a) => `${esc(a.area)}${a.categories.length ? ` (${a.categories.map(esc).join(", ")})` : ""}`).join("; ");
    body += kv([
      ["SJR", sjr],
      ["H-index", d.h_index != null ? esc(d.h_index) : ""],
      ["Cites/doc (2 years)", cites],
      ["Coverage", esc(d.coverage)],
      ["ISSN", esc((d.issns || []).join(", "))],
      ["Publisher", esc(d.publisher)],
      ["Country", esc(d.country)],
      ["Type", esc(d.publication_type)],
      ["Subject areas", areas],
      ["Links", [link(d.homepage, "Journal homepage"), link(d.how_to_publish, "How to publish")].filter(Boolean).join(" &middot; ")],
    ]);
    if (d.notice) body += `<p class="note">${esc(d.notice)}</p>`;
    if ((d.category_quartiles || []).length) {
      body += `<table><thead><tr><th>Category</th><th>Year</th><th>Quartile</th></tr></thead><tbody>${d.category_quartiles
        .map((q) => `<tr><td>${esc(q.category)}</td><td>${esc(q.year)}</td><td><span class="tag ${tone(q.quartile)}">${esc(q.quartile)}</span></td></tr>`).join("")}</tbody></table>`;
    }
    if ((d.similar_journals || []).length) {
      body += `<p class="note" style="margin-top:12px"><strong>Similar journals</strong> (SCImago, by shared references)</p><ul class="similar">${d.similar_journals
        .map((s) => `<li>${link(s.url, s.title)} <span class="ctx">${esc(s.similarity || "")}</span>${s.open_access ? `<span class="tag neutral">OA</span>` : ""}</li>`).join("")}</ul>`;
    }
  }
  return card("SCImago", "Existence and indexing, SJR / quartile, H-index, citations per document, related journals.", r, body, { site: "SCImago", wide: true });
}

function crestCard(r) {
  const d = r.data || {};
  let body = "";
  let override;
  if (r.status === "found") {
    override = d.eligible ? ["Eligible for subsidy", "good"] : [d.status, "bad"];
    body = kv([
      ["Status", `<span class="tag ${d.eligible ? "good" : "bad"}">${esc(d.status)}</span>`],
      ["Reason", esc(d.reason)],
      ["Listed as", esc(d.title)],
      ["Publisher", esc(d.publisher)],
      ["ISSN / eISSN", esc([d.issn, d.eissn].filter(Boolean).join(" / "))],
      ["Journal ID", esc(d.journal_id)],
    ]);
  } else if (r.status === "not_found") {
    override = ["Not on list", "bad"];
  }
  return card("CREST - DHET subsidy", "Whether 2026 articles in this journal are eligible for DHET subsidy.", r, body, { site: "CREST", chip: override });
}

function kanalCard(r) {
  const d = r.data || {};
  let body = "";
  let override;
  if (r.status === "found") {
    const t = d.ceased ? "bad" : { "2": "good", "1": "good", "0": "bad", X: "warn" }[d.level] || "neutral";
    override = [d.level ? `Level ${d.level}` : "Listed", t];
    body = kv([
      ["Level", `${esc(d.level_meaning || d.level || "-")}`],
      ["Ceased", d.ceased ? esc(`Publication ceased in ${d.ceased}`) : ""],
      ["Decision", esc(d.decision_note)],
      ["DOAJ", d.doaj == null ? "" : d.doaj ? "Indexed in DOAJ" : "Not indexed in DOAJ"],
      ["Publisher", esc(d.publisher)],
      ["ISSN", esc((d.issns || []).join(", "))],
      ["Website", link(d.homepage)],
    ]);
    if ((d.level_history || []).length) {
      body += `<table><thead><tr>${d.level_history.map((h) => `<th>${esc(h.year)}</th>`).join("")}</tr></thead><tbody><tr>${d.level_history
        .map((h) => `<td>${esc(h.level)}</td>`).join("")}</tr></tbody></table>`;
    }
  } else if (r.status === "not_found") {
    override = ["Not listed", "warn"];
  }
  return card("Kanalregister", "Credibility: Norwegian Register level (2 leading, 1 approved, 0 not approved, X under discussion).", r, body, { site: "Kanalregister", chip: override });
}

function sanlicCard(r) {
  const d = r.data || {};
  let body = "";
  let override;
  if (r.status === "found") {
    override = d.included ? ["Covered", "good"] : ["Not covered", "warn"];
    body = kv([
      ["Included in R&P agreement", yesno(d.included)],
      ["Open access model", esc(d.oa_type)],
      ["Explanation", esc(d.explanation)],
      ["Publisher", esc(d.publisher)],
      ["Agreement period", esc(d.agreement_period)],
      ["Agreement details", link(d.agreement_page, "SANLiC agreement page")],
      ["Listed as", esc(d.title)],
    ]);
  } else if (r.status === "not_found") {
    override = ["Not covered", "warn"];
  }
  if (d.title_list) body += `<p class="src">${link(d.title_list, "SANLiC title list (xlsx)")}</p>`;
  return card("SANLiC", "Whether a SANLiC read-and-publish agreement covers the APC.", r, body, { site: "SANLiC", chip: override });
}

function siteCard(r) {
  const d = r.data || {};
  const metrics = Object.values(d.metrics || {});
  let body = "";
  if (metrics.length) {
    body = `<table><thead><tr><th>Statistic</th><th>Value</th><th>Read from</th></tr></thead><tbody>${metrics
      .map((m) => `<tr><td>${esc(m.label)}</td><td><strong>${esc(m.value)}</strong></td><td class="ctx" title="${esc(m.context)}">${link(m.url, new URL(m.url).pathname || m.url)}<br>&ldquo;${esc(m.context.slice(0, 110))}${m.context.length > 110 ? "&hellip;" : ""}&rdquo;</td></tr>`).join("")}</tbody></table>`;
  }
  if ((d.pages_checked || []).length) {
    body += `<p class="note">Pages read: ${d.pages_checked.map((u) => link(u)).join(", ")}</p>`;
  }
  return card("Journal website", "Days to decision, acceptance rate and APC as stated by the journal.", r, body, { site: "journal site", wide: true });
}

function tone(q) {
  return { Q1: "good", Q2: "good", Q3: "warn", Q4: "bad" }[q] || "neutral";
}

function render(report) {
  const id = report.identity || {};
  const s = report.sources;
  const meta = [
    (id.issns || []).length ? `ISSN ${id.issns.join(", ")}` : "",
    id.publisher, id.country,
    id.homepage ? link(id.homepage, "homepage") : "",
  ].filter(Boolean).map((x) => (x.startsWith("<a") ? x : esc(x))).join(" &middot; ");
  const glance = (report.glance || []).map((g) => `<div class="tile ${esc(g.tone)}"><div class="l">${esc(g.label)}</div><div class="v">${esc(g.value)}</div><div class="s">${esc(g.source.replace("_", " "))}</div></div>`).join("");
  $("#report").innerHTML = `
    <div class="identity"><h2>${esc(id.title || report.query)}</h2><div class="meta">${meta}</div></div>
    <div class="glance">${glance}</div>
    <div class="cards">
      ${scimagoCard(s.scimago)}
      ${crestCard(s.crest)}
      ${kanalCard(s.kanalregister)}
      ${sanlicCard(s.sanlic)}
      ${siteCard(s.journal_site)}
    </div>`;
  $("#report").hidden = false;
}

function renderCandidates(report) {
  const items = report.candidates.map((c) => `<li><button type="button" data-sid="${esc(c.source_id)}">${esc(c.title)}<small>${esc([c.country, c.publisher].filter(Boolean).join(" · "))}</small></button></li>`).join("");
  $("#candidates").innerHTML = `<h2>Several journals match &ldquo;${esc(report.query)}&rdquo;</h2><p class="note">Choose one (from SCImago):</p><ul class="candidates">${items}</ul>`;
  $("#candidates").hidden = false;
  $("#candidates").querySelectorAll("button[data-sid]").forEach((b) =>
    b.addEventListener("click", () => run({ q: report.query, sid: b.dataset.sid, site: $("#site").value.trim() })));
}

async function run(params) {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v));
  history.replaceState(null, "", "?" + qs.toString());
  $("#go").disabled = true;
  $("#report").hidden = true;
  $("#candidates").hidden = true;
  const status = $("#status");
  status.className = "status";
  status.textContent = "Checking SCImago, CREST, Kanalregister, SANLiC and the journal website. This usually takes 15-60 seconds.";
  status.hidden = false;
  try {
    const res = await fetch("/api/report?" + qs.toString());
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
    const report = await res.json();
    status.hidden = true;
    if ((report.candidates || []).length) renderCandidates(report);
    else render(report);
  } catch (err) {
    status.className = "status error";
    status.textContent = "Lookup failed: " + err.message;
  } finally {
    $("#go").disabled = false;
  }
}

$("#search").addEventListener("submit", (e) => {
  e.preventDefault();
  run({ q: $("#q").value.trim(), site: $("#site").value.trim() });
});

if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "127.0.0.1" || location.hostname === "localhost")) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

// Support shareable links: /?q=1532-0464
(() => {
  const p = new URLSearchParams(location.search);
  if (p.get("q")) {
    $("#q").value = p.get("q");
    if (p.get("site")) $("#site").value = p.get("site");
    run({ q: p.get("q"), sid: p.get("sid"), site: p.get("site") });
  }
})();
