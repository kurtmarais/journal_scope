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
  working: ["Checking…", "neutral"],
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

function scopusCard(r) {
  const d = r.data || {};
  const body = [];
  if (r.status === "found") {
    const cs = d.citescore;
    const pct = d.best_percentile != null ? `, best: ${Math.round(d.best_percentile)}th percentile` : "";
    body.push(kv([
      ["CiteScore", cs && cs.value != null ? [String(cs.value), " ", d.best_quartile && tag(d.best_quartile, quartileTone(d.best_quartile)),
        h("span", { class: "ctx" }, ` (${cs.year || ""}${pct})`)] : ""],
      ["CiteScore Tracker", d.citescore_tracker && d.citescore_tracker.value != null ? `${d.citescore_tracker.value} (${d.citescore_tracker.year}, in progress)` : ""],
      ["SJR", d.sjr ? `${d.sjr.value} (${d.sjr.year})` : ""],
      ["Coverage", d.coverage],
      ["ISSN", (d.issns || []).join(", ")],
      ["Publisher", d.publisher],
      ["Type", d.type],
      ["Links", join([d.homepage && link(d.homepage, "Journal homepage"), d.scopus_url && link(d.scopus_url, "Scopus source page")])],
    ]));
    if ((d.subject_ranks || []).length) {
      body.push(h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Subject"), h("th", {}, "Rank"), h("th", {}, "Percentile"), h("th", {}, "Quartile"))),
        h("tbody", {}, d.subject_ranks.map((x) => h("tr", {}, h("td", {}, x.subject), h("td", {}, String(x.rank ?? "")),
          h("td", {}, x.percentile != null ? `${Math.round(x.percentile)}th` : ""), h("td", {}, x.quartile ? tag(x.quartile, quartileTone(x.quartile)) : ""))))));
    } else if (d.ranks_unavailable) {
      body.push(h("p", { class: "note" }, "Subject ranks and quartiles were not available for this lookup."));
    }
  }
  return card("Scopus", "Existence and indexing, CiteScore and its quartile in each subject (Q1 = 75th percentile and above), SJR.", r, body, { site: "Scopus", wide: true });
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

// ---------------------------------------------------- templates & guidelines
// Links only, built from the journal's name, publisher and homepage.
const PUBLISHERS = [
  { key: "elsevier", name: "Elsevier", match: /elsevier|academic press|cell press|pergamon/i, host: /sciencedirect|elsevier/,
    templates: "https://www.elsevier.com/researcher/author/policies-and-guidelines/latex-instructions",
    overleaf: "https://www.overleaf.com/latex/templates/tagged/elsevier" },
  { key: "springer", name: "Springer Nature", match: /springer|biomed central|\bbmc\b|nature portfolio|palgrave/i, host: /springer|biomedcentral|nature\.com/,
    templates: "https://www.springernature.com/gp/authors/campaigns/latex-author-support",
    overleaf: "https://www.overleaf.com/gallery/tagged/springer" },
  { key: "wiley", name: "Wiley", match: /wiley|blackwell|hindawi/i, host: /wiley/,
    templates: null, overleaf: "https://www.overleaf.com/gallery/tagged/wiley-official" },
  { key: "tandf", name: "Taylor & Francis", match: /taylor|francis|routledge/i, host: /tandfonline/,
    templates: "https://authorservices.taylorandfrancis.com/publishing-your-research/writing-your-paper/formatting-and-templates/" },
  { key: "sage", name: "SAGE", match: /\bsage\b/i, host: /sagepub/,
    templates: "https://www.sagepub.com/journals/information-for-authors/preparing-your-manuscript" },
  { key: "ieee", name: "IEEE", match: /\bieee\b|institute of electrical/i, host: /ieee/,
    templates: "https://template-selector.ieee.org/" },
  { key: "mdpi", name: "MDPI", match: /\bmdpi\b/i, host: /mdpi\.com/, templates: "https://www.mdpi.com/authors/layout" },
  { key: "frontiers", name: "Frontiers", match: /frontiers/i, host: /frontiersin/, templates: "https://www.frontiersin.org/guidelines/author-guidelines" },
  { key: "oup", name: "Oxford University Press", match: /oxford university press|\boup\b/i, host: /academic\.oup\.com/,
    templates: "https://academic.oup.com/pages/for-authors/journals/preparing-and-submitting-your-manuscript" },
];

export function journalGuidelinesUrl(homepage) {
  let u;
  try { u = new URL(homepage); } catch { return null; }
  const seg = u.pathname.split("/").filter(Boolean);
  const after = (name) => (seg.indexOf(name) >= 0 ? seg[seg.indexOf(name) + 1] : null);
  const host = u.hostname;
  if (/sciencedirect\.com$/.test(host) && after("journal")) return `https://www.sciencedirect.com/journal/${after("journal")}/publish/guide-for-authors`;
  if (/journals\.elsevier\.com$/.test(host) && seg[0]) return `https://www.sciencedirect.com/journal/${seg[0]}/publish/guide-for-authors`;
  if (/springer\.com$/.test(host)) { const m = /\/journal\/(\d+)/.exec(u.pathname); if (m) return `https://link.springer.com/journal/${m[1]}/submission-guidelines`; }
  if (/mdpi\.com$/.test(host) && after("journal")) return `https://www.mdpi.com/journal/${after("journal")}/instructions`;
  if (/tandfonline\.com$/.test(host)) {
    const code = after("journals") || after("toc") || after("loi") || new URLSearchParams(u.search).get("journalCode");
    if (code) return `https://www.tandfonline.com/action/authorSubmission?show=instructions&journalCode=${code}`;
  }
  if (/frontiersin\.org$/.test(host) && after("journals")) return `https://www.frontiersin.org/journals/${after("journals")}/for-authors/author-guidelines`;
  if (/sagepub\.com$/.test(host) && after("home")) return `https://journals.sagepub.com/author-instructions/${after("home").toUpperCase()}`;
  if (/onlinelibrary\.wiley\.com$/.test(host) && after("journal")) return `https://onlinelibrary.wiley.com/page/journal/${after("journal")}/homepage/forauthors.html`;
  return null;
}

export function templateLinks(report) {
  const id = report.identity || {};
  const s = report.sources || {};
  const data = (k) => ((s[k] && s[k].status === "found" && s[k].data) || {});
  const title = id.title || report.query || "";
  const publisher = id.publisher || data("scopus").publisher || data("crest").publisher || data("kanalregister").publisher || "";
  const homepage = id.homepage || data("scopus").homepage || data("kanalregister").homepage || (data("journal_site").homepage) || "";
  let host = "";
  try { host = new URL(homepage).hostname; } catch { /* no homepage */ }
  const pub = PUBLISHERS.find((p) => p.match.test(publisher) || (host && p.host.test(host)));
  const links = [];
  const guide = journalGuidelinesUrl(homepage);
  if (guide) links.push({ label: "Author guidelines for this journal", note: "templates are usually linked here", url: guide });
  else if (homepage) links.push({ label: "Journal homepage", note: "look for 'Guide for authors' or 'Submission guidelines'", url: homepage });
  if (pub && pub.templates) links.push({ label: `${pub.name} templates and formatting`, note: "the publisher's Word/LaTeX templates", url: pub.templates });
  if (pub && pub.overleaf) links.push({ label: `${pub.name} templates on Overleaf`, note: "official LaTeX templates", url: pub.overleaf });
  if (title) {
    links.push({ label: "Search Overleaf for this journal's template", note: "LaTeX, via a web search of Overleaf's gallery",
      url: `https://www.google.com/search?q=${encodeURIComponent(`site:overleaf.com/latex/templates "${title}"`)}` });
    links.push({ label: "Citation style (Zotero / Mendeley / EndNote)", note: "the journal's reference style, if listed",
      url: `https://www.zotero.org/styles?q=${encodeURIComponent(title)}` });
  }
  return links;
}

function templatesCard(report) {
  const links = templateLinks(report);
  if (!links.length) return null;
  return h("article", { class: "card templates" },
    h("header", {}, h("h3", {}, "Templates & author guidelines")),
    h("p", { class: "purpose" }, "Where to get this journal's manuscript template and reference style."),
    h("ul", { class: "tlinks" }, links.map((l) => h("li", {}, link(l.url, l.label), l.note && h("span", { class: "ctx" }, ` (${l.note})`)))));
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
    report.timings && Object.keys(report.timings).length > 0 && h("p", { class: "timings" },
      "Time taken: " + Object.entries(report.timings).map(([k, v]) => `${{ scopus: "Scopus", crest: "CREST", sanlic: "SANLiC", kanalregister: "Kanalregister", journal_site: "journal site" }[k] || k} ${v} s`).join(" · ")),
    h("div", { class: "cards" },
      s.scopus ? scopusCard(s.scopus) : s.scimago ? scimagoCard(s.scimago) : null, crestCard(s.crest), kanalCard(s.kanalregister), sanlicCard(s.sanlic), siteCard(s.journal_site), templatesCard(report)));
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
// The phone gets Scopus data through a Supabase Edge Function (it holds the
// Elsevier API key) and searches the CREST and SANLiC lists the PC loads into
// Supabase, then builds a report in the same shape as the PC's.

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

// --- Scopus (Elsevier Serial Title API), same parsing as app/sources/scopus.py
const asList = (v) => (v == null ? [] : Array.isArray(v) ? v : [v]);
const num = (v) => (v == null || v === "" || isNaN(Number(v)) ? null : Number(v));
export const scopusQuartile = (p) => (p == null ? null : p >= 75 ? "Q1" : p >= 50 ? "Q2" : p >= 25 ? "Q3" : "Q4");

export function scopusEntries(payload) {
  const resp = (payload && payload["serial-metadata-response"]) || {};
  return asList(resp.entry).filter((e) => e && !e.error && (e["dc:title"] || e["source-id"]));
}

function issnsOf(e) {
  return [e["prism:issn"], e["prism:eIssn"]].map(normaliseIssn).filter(Boolean)
    .filter((v, i, a) => a.indexOf(v) === i);
}

export function scopusCandidates(items) {
  return items.map((e) => ({ source_id: issnsOf(e)[0], title: e["dc:title"] || "", publisher: e["dc:publisher"] || null, country: null }))
    .filter((c) => c.source_id);
}

export function parseScopusEntry(e, limited = false) {
  const subjects = Object.fromEntries(asList(e["subject-area"]).filter((x) => x["@code"]).map((x) => [String(x["@code"]), x.$]));
  const links = Object.fromEntries(asList(e.link).map((l) => [l["@ref"], l["@href"]]));
  const cs = e.citeScoreYearInfoList || {};
  let citescore = cs.citeScoreCurrentMetric != null ? { year: num(cs.citeScoreCurrentMetricYear), value: num(cs.citeScoreCurrentMetric) } : null;
  const tracker = cs.citeScoreTracker != null ? { year: num(cs.citeScoreTrackerYear), value: num(cs.citeScoreTracker) } : null;
  const ranks = [];
  const complete = asList(cs.citeScoreYearInfo).filter((y) => (y["@status"] || "").toLowerCase() === "complete")
    .sort((a, b) => num(b["@year"]) - num(a["@year"]));
  if (complete.length) {
    const year = num(complete[0]["@year"]);
    for (const il of asList(complete[0].citeScoreInformationList)) {
      for (const info of asList(il.citeScoreInfo)) {
        if ((info.docType || "all") !== "all") continue;
        if (!citescore || citescore.year === year) citescore = { year, value: num(info.citeScore) };
        for (const r of asList(info.citeScoreSubjectRank)) {
          const p = num(r.percentile);
          ranks.push({ subject: subjects[String(r.subjectCode)] || String(r.subjectCode), code: String(r.subjectCode), year,
            rank: r.rank, percentile: p, quartile: scopusQuartile(p) });
        }
      }
    }
  }
  ranks.sort((a, b) => (b.percentile || 0) - (a.percentile || 0));
  const sjr = asList((e.SJRList || {}).SJR).map((x) => ({ year: num(x["@year"]), value: num(x.$) }))
    .filter((x) => x.year && x.value != null).sort((a, b) => b.year - a.year)[0] || null;
  const start = e.coverageStartYear, end = e.coverageEndYear;
  return {
    title: e["dc:title"] || "", publisher: e["dc:publisher"] || null, source_id: e["source-id"] ? String(e["source-id"]) : null,
    type: e["prism:aggregationType"] || null, issns: issnsOf(e), subjects: Object.values(subjects),
    citescore, citescore_tracker: tracker, subject_ranks: ranks,
    best_quartile: ranks.length ? ranks[0].quartile : null, best_percentile: ranks.length ? ranks[0].percentile : null,
    sjr, coverage: start && end ? `${start}-${end}` : null, coverage_last_year: num(end),
    homepage: links.homepage || null, scopus_url: links["scopus-source"] || null,
    ranks_unavailable: !ranks.length && (limited || citescore != null),
  };
}

function scopusSource(d, note, error) {
  const url = (d && d.scopus_url) || "https://www.scopus.com/sources";
  if (error) return { status: "error", url, data: {}, error };
  if (!d) return { status: "not_found", url, data: {}, note: "Not in Scopus, so the journal is not (or no longer) indexed there." };
  return { status: "found", url, data: d, note };
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

export function quickReport(query, { scopus, scopusNote, scopusError, crest, sanlic, lists, issns, title }) {
  const meta = Object.fromEntries((lists || []).map((l) => [l.key, l]));
  const sj = scopusSource(scopus, scopusNote, scopusError);
  const kanalTerm = (issns || [])[0] || title || query;
  const sources = {
    scopus: sj,
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
    const last = d.coverage_last_year || (d.citescore && d.citescore.year);
    glance.push({ label: "Indexed in Scopus", value: d.coverage ? `Coverage ${d.coverage}` : "Listed",
      tone: last >= new Date().getFullYear() - 2 ? "good" : "warn", source: "scopus" });
    if (d.citescore && d.citescore.value != null) {
      const extra = d.best_quartile && d.best_percentile != null ? ` (${d.best_quartile}, ${Math.round(d.best_percentile)}th percentile)` : "";
      glance.push({ label: d.citescore.year ? `CiteScore ${d.citescore.year}` : "CiteScore", value: `${d.citescore.value.toFixed(1)}${extra}`,
        tone: { Q1: "good", Q2: "good", Q3: "warn", Q4: "bad" }[d.best_quartile] || "neutral", source: "scopus" });
    }
    if (d.sjr) glance.push({ label: `SJR ${d.sjr.year}`, value: d.sjr.value.toFixed(3), tone: "neutral", source: "scopus" });
  } else if (sj.status === "not_found") {
    glance.push({ label: "Indexed in Scopus", value: "Not found", tone: "bad", source: "scopus" });
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
