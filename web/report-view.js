// Renders a Journal Scope report. Shared by the desktop window and the phone app.
// Builds DOM nodes directly (no innerHTML), so it works under a strict CSP.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false || c === "") continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const SAFE = /^(https?:|mailto:)/i;
export function link(href, text) {
  if (!href || !SAFE.test(href)) return text ? document.createTextNode(text) : null;
  return h("a", { href, target: "_blank", rel: "noopener noreferrer" }, text || href);
}

function kv(pairs) {
  const rows = pairs.filter(([, v]) => v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && !v.length));
  if (!rows.length) return null;
  return h("dl", { class: "kv" }, rows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
}

const join = (nodes, sep = " · ") => nodes.filter(Boolean).flatMap((n, i) => (i ? [sep, n] : [n]));
const tag = (text, tone) => h("span", { class: `tag ${tone}` }, text);
export const quartileTone = (q) => ({ Q1: "good", Q2: "good", Q3: "warn", Q4: "bad" }[q] || "neutral");

const STATUS_CHIP = {
  found: ["Found", "good"],
  not_found: ["Not found", "warn"],
  error: ["Could not check", "bad"],
  skipped: ["Skipped", "neutral"],
};

function when(iso) {
  return iso ? iso.replace("T", " ").replace("+00:00", " UTC") : "";
}

function card(title, purpose, r, body, opts = {}) {
  const [text, tone] = opts.chip || STATUS_CHIP[r.status] || [r.status, "neutral"];
  return h("article", { class: `card ${opts.wide ? "wide" : ""}` },
    h("header", {}, h("h3", {}, title), h("span", { class: `chip ${tone}` }, text)),
    h("p", { class: "purpose" }, purpose),
    body,
    r.note && h("p", { class: "note" }, r.note),
    r.error && h("p", { class: "err" }, r.error),
    r.url && h("p", { class: "src" }, link(r.url, `Open on ${opts.site || "source"}`),
      r.checked_at && h("span", { class: "ctx" }, ` checked ${when(r.checked_at)}`)),
  );
}

function scimagoCard(r) {
  const d = r.data || {};
  const body = [];
  if (r.status === "found") {
    const sjr = d.sjr && [String(d.sjr.value ?? "-"), " ", d.sjr.best_quartile && tag(d.sjr.best_quartile, quartileTone(d.sjr.best_quartile)),
      h("span", { class: "ctx" }, ` (${d.sjr.year}, best quartile)`)];
    const cites = (d.cites_per_doc_2y || []).map((c) => `${c.year}: ${c.value}`).join(" · ");
    const areas = (d.subject_areas || []).map((a) => a.area + (a.categories.length ? ` (${a.categories.join(", ")})` : "")).join("; ");
    body.push(kv([
      ["SJR", sjr],
      ["H-index", d.h_index != null ? String(d.h_index) : ""],
      ["Cites/doc (2 years)", cites],
      ["Coverage", d.coverage],
      ["ISSN", (d.issns || []).join(", ")],
      ["Publisher", d.publisher],
      ["Country", d.country],
      ["Type", d.publication_type],
      ["Subject areas", areas],
      ["Links", join([link(d.homepage, "Journal homepage"), link(d.how_to_publish, "How to publish")])],
    ]));
    if (d.notice) body.push(h("p", { class: "note" }, d.notice));
    if ((d.category_quartiles || []).length) {
      body.push(h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Category"), h("th", {}, "Year"), h("th", {}, "Quartile"))),
        h("tbody", {}, d.category_quartiles.map((q) =>
          h("tr", {}, h("td", {}, q.category), h("td", {}, String(q.year)), h("td", {}, tag(q.quartile, quartileTone(q.quartile))))))));
    }
    if ((d.similar_journals || []).length) {
      body.push(h("p", { class: "note gap" }, h("strong", {}, "Similar journals"), " (SCImago, by shared references)"));
      body.push(h("ul", { class: "similar" }, d.similar_journals.map((s) =>
        h("li", {}, link(s.url, s.title), " ", h("span", { class: "ctx" }, s.similarity || ""), s.open_access && tag("OA", "neutral")))));
    }
  }
  return card("SCImago", "Existence and indexing, SJR / quartile, H-index, citations per document, related journals.", r, body, { site: "SCImago", wide: true });
}

function crestCard(r) {
  const d = r.data || {};
  let body = null;
  let chip;
  if (r.status === "found") {
    chip = d.eligible ? ["Eligible for subsidy", "good"] : [d.status, "bad"];
    body = kv([
      ["Status", tag(d.status, d.eligible ? "good" : "bad")],
      ["Reason", d.reason],
      ["Listed as", d.title],
      ["Publisher", d.publisher],
      ["ISSN / eISSN", [d.issn, d.eissn].filter(Boolean).join(" / ")],
      ["Journal ID", d.journal_id],
    ]);
  } else if (r.status === "not_found") {
    chip = ["Not on list", "bad"];
  }
  return card("CREST - DHET subsidy", "Whether 2026 articles in this journal are eligible for DHET subsidy.", r, body, { site: "CREST", chip });
}

function kanalCard(r) {
  const d = r.data || {};
  const body = [];
  let chip;
  if (r.status === "found") {
    const tone = d.ceased ? "bad" : { 2: "good", 1: "good", 0: "bad", X: "warn" }[d.level] || "neutral";
    chip = [d.level ? `Level ${d.level}` : "Listed", tone];
    body.push(kv([
      ["Level", d.level_meaning || d.level || "-"],
      ["Ceased", d.ceased ? `Publication ceased in ${d.ceased}` : ""],
      ["Decision", d.decision_note],
      ["DOAJ", d.doaj == null ? "" : d.doaj ? "Indexed in DOAJ" : "Not indexed in DOAJ"],
      ["Publisher", d.publisher],
      ["ISSN", (d.issns || []).join(", ")],
      ["Website", link(d.homepage)],
    ]));
    if ((d.level_history || []).length) {
      body.push(h("div", { class: "scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, d.level_history.map((x) => h("th", {}, String(x.year))))),
        h("tbody", {}, h("tr", {}, d.level_history.map((x) => h("td", {}, x.level)))))));
    }
  } else if (r.status === "not_found") {
    chip = ["Not listed", "warn"];
  }
  return card("Kanalregister", "Credibility: Norwegian Register level (2 leading, 1 approved, 0 not approved, X under discussion).", r, body, { site: "Kanalregister", chip });
}

function sanlicCard(r) {
  const d = r.data || {};
  const body = [];
  let chip;
  if (r.status === "found") {
    chip = d.included ? ["Covered", "good"] : ["Not covered", "warn"];
    body.push(kv([
      ["Included in R&P agreement", d.included ? "Yes" : "No"],
      ["Open access model", d.oa_type],
      ["Explanation", d.explanation],
      ["Publisher", d.publisher],
      ["Agreement period", d.agreement_period],
      ["Agreement details", link(d.agreement_page, "SANLiC agreement page")],
      ["Listed as", d.title],
    ]));
  } else if (r.status === "not_found") {
    chip = ["Not covered", "warn"];
  }
  if (d.title_list) body.push(h("p", { class: "src" }, link(d.title_list, "SANLiC title list (xlsx)")));
  return card("SANLiC", "Whether a SANLiC read-and-publish agreement covers the APC.", r, body, { site: "SANLiC", chip });
}

function siteCard(r) {
  const d = r.data || {};
  const metrics = Object.values(d.metrics || {});
  const body = [];
  if (metrics.length) {
    body.push(h("table", { class: "metrics" },
      h("thead", {}, h("tr", {}, h("th", {}, "Statistic"), h("th", {}, "Value"), h("th", {}, "Read from"))),
      h("tbody", {}, metrics.map((m) => {
        let path = m.url;
        try { path = new URL(m.url).pathname || m.url; } catch { /* keep */ }
        const ctx = m.context.length > 110 ? m.context.slice(0, 110) + "…" : m.context;
        return h("tr", {}, h("td", {}, m.label), h("td", {}, h("strong", {}, m.value)),
          h("td", { class: "ctx", title: m.context }, link(m.url, path), h("br"), `“${ctx}”`));
      }))));
  }
  if ((d.pages_checked || []).length) {
    body.push(h("p", { class: "note" }, "Pages read: ", join(d.pages_checked.map((u) => link(u)), ", ")));
  }
  return card("Journal website", "Days to decision, acceptance rate and APC as stated by the journal.", r, body, { site: "journal site", wide: true });
}

export function glanceTiles(glance) {
  return h("div", { class: "glance" }, (glance || []).map((g) =>
    h("div", { class: `tile ${g.tone}` },
      h("div", { class: "l" }, g.label), h("div", { class: "v" }, g.value), h("div", { class: "s" }, g.source.replace("_", " ")))));
}

export function renderReport(report) {
  const id = report.identity || {};
  const s = report.sources;
  const meta = join([
    (id.issns || []).length ? `ISSN ${id.issns.join(", ")}` : "",
    id.publisher, id.country,
    id.homepage ? link(id.homepage, "homepage") : "",
  ].filter(Boolean));
  return h("div", { class: "report" },
    h("div", { class: "identity" }, h("h2", {}, id.title || report.query), h("div", { class: "meta" }, meta)),
    glanceTiles(report.glance),
    h("div", { class: "cards" },
      scimagoCard(s.scimago), crestCard(s.crest), kanalCard(s.kanalregister), sanlicCard(s.sanlic), siteCard(s.journal_site)));
}

export function renderCandidates(query, candidates, onPick) {
  return h("div", { class: "candidates-box" },
    h("h2", {}, `Several journals match “${query}”`),
    h("p", { class: "note" }, "Choose one (from SCImago):"),
    h("ul", { class: "candidates" }, candidates.map((c) =>
      h("li", {}, h("button", { type: "button", onclick: () => onPick(c) },
        c.title, h("small", {}, [c.country, c.publisher].filter(Boolean).join(" · ")))))));
}
