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
  skipped: ["Not checked", "neutral"],
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
      ["Links", join([d.homepage && link(d.homepage, "Journal homepage"), d.how_to_publish && link(d.how_to_publish, "How to publish")])],
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


// ------------------------------------------------------------- phone lists
// The phone searches reference lists the PC loads into Supabase (SCImago export,
// CREST DHET list, SANLiC title list) and builds a report in the same shape as
// the PC's, so renderReport() shows it.

export function normaliseTitle(v) {
  if (!v) return "";
  let s = v.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
  s = s.replace(/&/g, " and ").replace(/,\s*the\s*$/, "").replace(/^the\s+/, "");
  return s.replace(/[^a-z0-9]+/g, " ").replace(/\s+/g, " ").trim();
}

export function normaliseIssn(v) {
  const m = /^\s*(\d{4})[-\s]?(\d{3}[\dXx])\s*$/.exec(v || "");
  return m ? `${m[1]}-${m[2].toUpperCase()}` : null;
}

const SANLIC_PAGES = {
  ELSEVIER: "sciencedirect-elsevier", "TAYLOR & FRANCIS": "taylor-francis", SPRINGER: "springer", WILEY: "wiley",
  SAGE: "sage-publishing", OUP: "oxford-university-press-journals", CUP: "cambridge-university-press", EMERALD: "emerald",
  BENTHAM: "bentham-science-publishers-2", ACS: "american-chemical-society-acs", ACM: "association-for-computing-machinery-acm",
  IOP: "iopscience-extra", RSC: "royal-society-of-chemistry", AIP: "american-institute-of-physics-2",
  "ROYAL SOCIETY": "royal-society", "THE COMPANY OF BIOLOGIST": "the-company-of-biologists",
  "THE COMPANY OF BIOLOGISTS": "the-company-of-biologists",
};

const loadedNote = (meta) => meta ? `From the ${meta.version || "current"} list, loaded ${new Date(meta.refreshed_at).toLocaleDateString()}.` : null;

function scimagoSource(r, meta) {
  if (!r) return { status: "not_found", url: "https://www.scimagojr.com/", data: {},
    note: "Not in SCImago's journal list, so not (or no longer) indexed in Scopus." };
  const years = (r.coverage || "").match(/\b(19|20)\d{2}\b/g) || [];
  const cats = (r.categories || "").split(";").map((c) => c.trim()).filter(Boolean).map((c) => {
    const m = /^(.*?)\s*\((Q[1-4])\)$/.exec(c);
    return { category: m ? m[1] : c, year: r.sjr_year, quartile: m ? m[2] : "-" };
  });
  return {
    status: "found",
    url: `https://www.scimagojr.com/journalsearch.php?q=${encodeURIComponent(r.key)}&tip=sid&clean=0`,
    note: (loadedNote(meta) || "") + " Homepage and similar journals are on SCImago's page.",
    data: {
      title: r.title, country: r.country, publisher: r.publisher, issns: r.issns || [], publication_type: r.type,
      sjr: r.sjr_year ? { year: r.sjr_year, value: r.sjr, best_quartile: r.sjr_quartile } : null,
      h_index: r.h_index, coverage: r.coverage, coverage_last_year: years.length ? Math.max(...years.map(Number)) : null,
      cites_per_doc_2y: r.cites_doc_2y != null ? [{ year: r.sjr_year, value: r.cites_doc_2y }] : [],
      category_quartiles: cats,
      subject_areas: (r.areas || "").split(";").map((a) => a.trim()).filter(Boolean).map((a) => ({ area: a, categories: [] })),
    },
  };
}

function crestSource(r, meta, title) {
  const url = "https://db.crest.sun.ac.za/zapublications/";
  if (!r) return { status: "not_found", url, data: { eligible: false },
    note: "Not on the DHET-approved journal list, so articles are not eligible for subsidy. " + (loadedNote(meta) || "") };
  const eligible = /eligible for subsidy|^active$/i.test(r.status || "");
  return { status: "found", url, note: loadedNote(meta), data: {
    title: r.title, publisher: r.publisher, issn: r.issn, eissn: r.eissn, journal_id: r.key,
    status: r.status || "Listed", eligible, reason: r.reason } };
}

function sanlicSource(r, meta) {
  const url = "https://sanlic.ac.za/read-and-publish-agreements/";
  if (!r) return { status: "not_found", url, data: { included: false, title_list: meta && meta.source_url },
    note: "Not on SANLiC's list of journals covered by R&P agreements; the APC is not covered by SANLiC. " + (loadedNote(meta) || "") };
  const page = SANLIC_PAGES[(r.publisher || "").trim().toUpperCase()];
  return { status: "found", url, note: loadedNote(meta), data: {
    title: r.title, included: r.included, oa_type: r.oa_type, explanation: r.explanation, publisher: r.publisher,
    agreement_page: page ? `https://sanlic.ac.za/${page}/` : null, title_list: meta && meta.source_url } };
}

export function quickReport(query, { scimago, crest, sanlic, lists, issns, title }) {
  const meta = Object.fromEntries((lists || []).map((l) => [l.key, l]));
  const sj = scimagoSource(scimago, meta.scimago);
  const kanalTerm = (issns || [])[0] || title || query;
  const sources = {
    scimago: sj,
    crest: crestSource(crest, meta.crest, title),
    sanlic: sanlicSource(sanlic, meta.sanlic),
    kanalregister: { status: "skipped", data: {},
      url: `https://kanalregister.hkdir.no/en/sok?option=journals&input=${encodeURIComponent(kanalTerm)}`,
      note: "Kanalregister builds its pages in a browser, so it is checked by your PC. Open it here, or get the full report from your PC." },
    journal_site: { status: "skipped", data: {},
      note: "Decision times, acceptance rate and APC come from the publisher's site, which your PC reads. Get the full report from your PC." },
  };
  const glance = [];
  if (sj.status === "found") {
    const d = sj.data;
    glance.push({ label: "Indexed (Scopus via SCImago)", value: d.coverage ? `Coverage ${d.coverage}` : "Listed",
      tone: d.coverage_last_year >= new Date().getFullYear() - 1 ? "good" : "warn", source: "scimago" });
    if (d.sjr) glance.push({ label: `SJR ${d.sjr.year}`, value: d.sjr.value != null ? `${d.sjr.value.toFixed(3)}${d.sjr.best_quartile ? ` (${d.sjr.best_quartile})` : ""}` : (d.sjr.best_quartile || "-"),
      tone: { Q1: "good", Q2: "good", Q3: "warn", Q4: "bad" }[d.sjr.best_quartile] || "neutral", source: "scimago" });
    if (d.cites_per_doc_2y.length) glance.push({ label: `Cites/doc, 2 years (${d.cites_per_doc_2y[0].year})`, value: d.cites_per_doc_2y[0].value.toFixed(2), tone: "neutral", source: "scimago" });
    if (d.h_index != null) glance.push({ label: "H-index", value: String(d.h_index), tone: "neutral", source: "scimago" });
  } else {
    glance.push({ label: "Indexed (Scopus via SCImago)", value: "Not found", tone: "bad", source: "scimago" });
  }
  const c = sources.crest;
  glance.push(c.status === "found" ? { label: "DHET subsidy", value: c.data.status, tone: c.data.eligible ? "good" : "bad", source: "crest" }
    : { label: "DHET subsidy", value: "Not on approved list", tone: "bad", source: "crest" });
  const n = sources.sanlic;
  glance.push(n.status === "found" ? { label: "SANLiC APC agreement", value: (n.data.included ? "Covered" : "Not covered") + (n.data.oa_type ? ` (${n.data.oa_type})` : ""), tone: n.data.included ? "good" : "warn", source: "sanlic" }
    : { label: "SANLiC APC agreement", value: "Not covered", tone: "warn", source: "sanlic" });
  return {
    query, quick: true, candidates: [], glance, sources,
    identity: { title: title || query, issns: issns || [], publisher: sj.data.publisher || (c.data && c.data.publisher), country: sj.data.country },
  };
}
