// "Find journals": keyword search across OpenAlex, DOAJ, CREST and Scopus.
// Shared by the desktop window and the phone app; each passes a `provider` that
// knows how to reach the services (desktop: its local server; phone: Supabase).
//
//   title mode : journals whose titles contain the keywords   (OpenAlex, DOAJ, CREST)
//   scope mode : journals that publish the most on the topic  (OpenAlex papers, Scopus papers, DOAJ keywords/subjects)
//
// Every result is matched against the CREST (DHET subsidy), SANLiC and SCImago (SJR quartile) lists.
import { apcTile, h, normaliseIssn, normaliseTitle, sjrSource, sjrTile } from "./report-view.js";

const SOURCE_LABEL = { openalex: "OpenAlex", doaj: "DOAJ", crest: "CREST", scopus: "Scopus" };
const SOURCES = { title: ["openalex", "doaj", "crest"], scope: ["openalex", "scopus", "doaj"] };

// ------------------------------------------------------------- parsing
function fromOpenAlexSource(s) {
  const st = s.summary_stats || {};
  return {
    title: s.display_name || "", publisher: s.host_organization_name || null,
    issns: (s.issn || []).map(normaliseIssn).filter(Boolean),
    homepage: s.homepage_url || null,
    openalex: { id: s.id, citedness: st["2yr_mean_citedness"] ?? null, h_index: st.h_index ?? null, works: s.works_count ?? null,
      apc_usd: s.apc_usd ?? null, is_oa: !!s.is_oa, in_doaj: !!s.is_in_doaj, country: s.country_code || null },
  };
}

function fromDoaj(r) {
  const b = r.bibjson || {};
  const apc = b.apc || {};
  const price = (apc.max || []).find((p) => p.currency === "USD") || (apc.max || [])[0];
  return {
    title: b.title || "", publisher: (b.publisher || {}).name || null,
    issns: [b.pissn, b.eissn].map(normaliseIssn).filter(Boolean),
    homepage: (b.ref || {}).journal || null,
    doaj: { apc: apc.has_apc === false ? "No APC" : price ? `${price.currency} ${price.price.toLocaleString()}` : null,
      weeks: b.publication_time_weeks ?? null, review: ((b.editorial || {}).review_process || [])[0] || null,
      url: (b.ref || {}).journal || null, has_apc: apc.has_apc ?? null, prices: apc.max || [] },
  };
}

function fromCrest(r) {
  return { title: r.title || "", publisher: r.publisher || null, issns: [r.issn, r.eissn].map(normaliseIssn).filter(Boolean) };
}

export function scopusFacetCategories(payload) {
  const sr = (payload && payload["search-results"]) || {};
  const facets = Array.isArray(sr.facet) ? sr.facet : sr.facet ? [sr.facet] : [];
  const out = [];
  for (const f of facets) {
    for (const c of Array.isArray(f.category) ? f.category : f.category ? [f.category] : []) {
      const title = c.label || c.name || c.value || c["@name"];
      const count = Number(c.hitCount ?? c["@hitCount"] ?? 0);
      if (title) out.push({ title, count });
    }
  }
  return out;
}

// ------------------------------------------------------------- merging
function merger() {
  const records = [];
  const byIssn = new Map();
  const byTitle = new Map();
  return {
    records,
    add(entry, source, rank, extra = {}) {
      const tnorm = normaliseTitle(entry.title);
      let rec = entry.issns.map((i) => byIssn.get(i)).find(Boolean) || (tnorm && byTitle.get(tnorm));
      if (!rec) {
        rec = { title: entry.title, publisher: entry.publisher, issns: [], sources: new Set(), ranks: {}, counts: {} };
        records.push(rec);
      }
      if (!rec.title || (source !== "crest" && entry.title && rec.sources.size === 1 && rec.sources.has("crest"))) rec.title = entry.title;
      rec.publisher = rec.publisher || entry.publisher;
      rec.homepage = rec.homepage || entry.homepage || null;
      for (const i of entry.issns) if (!rec.issns.includes(i)) { rec.issns.push(i); byIssn.set(i, rec); }
      if (tnorm) byTitle.set(tnorm, rec);
      if (entry.openalex) rec.openalex = entry.openalex;
      if (entry.doaj) rec.doaj = entry.doaj;
      rec.sources.add(source);
      if (rec.ranks[source] === undefined) rec.ranks[source] = rank;
      Object.assign(rec.counts, extra.counts || {});
      return rec;
    },
  };
}

function score(rec, mode, sizes, maxima) {
  const r = (s) => (rec.ranks[s] === undefined ? 0 : 1 - rec.ranks[s] / Math.max(sizes[s] || 1, 1));
  if (mode === "title") return r("openalex") + r("doaj") + r("crest") + 0.25 * rec.sources.size;
  const oa = maxima.openalex ? (rec.counts.openalex || 0) / maxima.openalex : 0;
  const sc = maxima.scopus ? (rec.counts.scopus || 0) / maxima.scopus : 0;
  return oa + sc + 0.4 * r("doaj");
}

export async function discover(mode, query, provider, limit = 60) {
  const notes = [];
  const m = merger();
  const sizes = {};
  const maxima = {};
  const calls = SOURCES[mode].map(async (source) => {
    try {
      if (source === "openalex") {
        const data = await provider.openalex(mode, query);
        if (mode === "title") {
          (data.sources || []).forEach((s, i) => m.add(fromOpenAlexSource(s), "openalex", i));
          sizes.openalex = (data.sources || []).length;
        } else {
          const details = new Map((data.sources || []).map((s) => [s.id, s]));
          (data.groups || []).forEach((g, i) => {
            const s = details.get(g.key);
            const entry = s ? fromOpenAlexSource(s) : { title: g.key_display_name || "", publisher: null, issns: [] };
            m.add(entry, "openalex", i, { counts: { openalex: g.count } });
          });
          sizes.openalex = (data.groups || []).length;
          maxima.openalex = Math.max(0, ...(data.groups || []).map((g) => g.count || 0));
        }
      } else if (source === "doaj") {
        const data = await provider.doaj(mode, query);
        (data.results || []).forEach((r, i) => m.add(fromDoaj(r), "doaj", i));
        sizes.doaj = (data.results || []).length;
      } else if (source === "crest") {
        const data = await provider.crestSearch(query);
        (data.results || []).forEach((r, i) => m.add(fromCrest(r), "crest", i));
        sizes.crest = (data.results || []).length;
      } else if (source === "scopus") {
        const cats = scopusFacetCategories(await provider.scopus(query));
        cats.forEach((c, i) => m.add({ title: c.title, publisher: null, issns: [] }, "scopus", i, { counts: { scopus: c.count } }));
        sizes.scopus = cats.length;
        maxima.scopus = Math.max(0, ...cats.map((c) => c.count));
      }
    } catch (e) {
      notes.push(`${SOURCE_LABEL[source]}: ${e.message}`);
    }
  });
  await Promise.all(calls);

  const records = m.records.map((rec) => ({ ...rec, score: score(rec, mode, sizes, maxima) }))
    .sort((a, b) => b.score - a.score).slice(0, limit);

  // DHET (CREST) and SANLiC status for every result.
  try {
    const issns = [...new Set(records.flatMap((r) => r.issns))];
    const titles = records.filter((r) => !r.issns.length).map((r) => r.title);
    const match = await provider.refmatch(issns, titles);
    for (const rec of records) {
      const tn = normaliseTitle(rec.title);
      rec.crest = (match.crest || []).find((c) => rec.issns.includes(c.issn) || rec.issns.includes(c.eissn) || c.title_norm === tn) || null;
      rec.sanlic = (match.sanlic || []).find((s) => (s.issns || []).some((i) => rec.issns.includes(i)) || s.title_norm === tn) || null;
      rec.sjr = (match.sjr || []).find((s) => (s.issns || []).some((i) => rec.issns.includes(i)) || s.title_norm === tn) || null;
      for (const i of [rec.crest && rec.crest.issn, rec.crest && rec.crest.eissn].filter(Boolean)) if (!rec.issns.includes(i)) rec.issns.push(i);
    }
    const errs = (match && match.errors) || {};
    if (errs.crest || errs.sanlic) for (const rec of records) rec.listsUnknown = true;
    if (errs.sjr || !("sjr" in (match || {}))) {
      for (const rec of records) rec.sjrUnknown = true;
      if (errs.sjr) notes.push(`SJR quartiles: ${errs.sjr}`);
    }
  } catch (e) {
    notes.push(`DHET/SANLiC lists: ${e.message}`);
    for (const rec of records) { rec.listsUnknown = true; rec.sjrUnknown = true; }
  }
  return { mode, query, results: records, notes };
}

// ------------------------------------------------------------- display
const eligible = (c) => !!c && /eligible for subsidy|^active$/i.test(c.status || "");

const QTONE = { Q1: "good", Q2: "good", Q3: "warn", Q4: "bad" };
const qRank = (rec) => (rec.sjr && rec.sjr.quartile ? Number(rec.sjr.quartile[1]) : null);

function chips(rec) {
  const out = [];
  if (rec.sjr && rec.sjr.quartile) out.push([`SJR ${rec.sjr.quartile}`, QTONE[rec.sjr.quartile]]);
  else if (rec.sjr) out.push(["SJR: no quartile", "neutral"]);
  else if (!rec.sjrUnknown) out.push(["Not ranked by SCImago", "warn"]);
  if (rec.listsUnknown && !rec.crest) out.push(["DHET: unknown", "neutral"]);
  else if (!rec.crest) out.push(["Not on DHET list", "bad"]);
  else out.push([eligible(rec.crest) ? "DHET eligible" : `DHET: ${rec.crest.status}`, eligible(rec.crest) ? "good" : "bad"]);
  if (rec.sanlic) out.push([rec.sanlic.included ? `SANLiC covered${rec.sanlic.oa_type ? ` (${rec.sanlic.oa_type})` : ""}` : "SANLiC: not covered", rec.sanlic.included ? "good" : "warn"]);
  if (rec.doaj && rec.doaj.apc) out.push([`OA · ${rec.doaj.apc}`, "neutral"]);
  else if (rec.openalex && rec.openalex.is_oa) out.push([`OA${rec.openalex.apc_usd ? ` · USD ${rec.openalex.apc_usd.toLocaleString()}` : ""}`, "neutral"]);
  else if (rec.openalex && rec.openalex.apc_usd) out.push([`APC USD ${rec.openalex.apc_usd.toLocaleString()} (list)`, "neutral"]);
  return out;
}

function details(rec, mode) {
  const parts = [];
  if (mode === "scope") {
    if (rec.counts.openalex) parts.push(`${rec.counts.openalex} matching papers (OpenAlex)`);
    if (rec.counts.scopus) parts.push(`${rec.counts.scopus} (Scopus)`);
  }
  const oa = rec.openalex;
  if (oa && oa.citedness != null) parts.push(`2-yr citedness ${oa.citedness.toFixed(1)}`);
  if (oa && oa.h_index != null) parts.push(`h-index ${oa.h_index}`);
  if (rec.doaj && rec.doaj.weeks != null) parts.push(`${rec.doaj.weeks} weeks to publication (DOAJ)`);
  return parts.join(" · ");
}

const SORTS = {
  relevance: (a, b) => b.score - a.score,
  sjr: (a, b) => ((b.sjr && b.sjr.sjr) ?? -1) - ((a.sjr && a.sjr.sjr) ?? -1),
  h_index: (a, b) => ((b.openalex && b.openalex.h_index) ?? -1) - ((a.openalex && a.openalex.h_index) ?? -1),
  apc: (a, b) => (apcUsd(a) ?? Infinity) - (apcUsd(b) ?? Infinity),
  title: (a, b) => a.title.localeCompare(b.title),
  citedness: (a, b) => ((b.openalex && b.openalex.citedness) || -1) - ((a.openalex && a.openalex.citedness) || -1),
  papers: (a, b) => ((b.counts.openalex || 0) + (b.counts.scopus || 0)) - ((a.counts.openalex || 0) + (a.counts.scopus || 0)),
};

/** APC in USD as far as the sources know it: 0 = free to publish, null = unknown. */
export function apcUsd(rec) {
  if (rec.doaj && rec.doaj.has_apc === false) return 0;
  const usd = rec.doaj && (rec.doaj.prices || []).find((p) => p.currency === "USD");
  if (usd && typeof usd.price === "number") return usd.price;
  if (rec.openalex && typeof rec.openalex.apc_usd === "number") return rec.openalex.apc_usd;
  return null;
}

/** A report built from a search result, so it can be saved (and shown) before a full check. */
export function recordToReport(rec) {
  const now = Date.now() / 1000;
  const c = rec.crest;
  const n = rec.sanlic;
  const sources = {
    scopus: { status: "skipped", data: {}, note: "Not checked yet: open the journal and use Check again for its full report." },
    crest: c ? { status: "found", data: { title: c.title, publisher: c.publisher, issn: c.issn, eissn: c.eissn, journal_id: c.key,
      status: c.status || "Listed", eligible: eligible(c), reason: c.reason || null } }
      : rec.listsUnknown ? { status: "skipped", data: {} } : { status: "not_found", data: { eligible: false } },
    sanlic: n ? { status: "found", data: { title: n.title, included: !!n.included, oa_type: n.oa_type, explanation: n.explanation, publisher: n.publisher } }
      : rec.listsUnknown ? { status: "skipped", data: {} } : { status: "not_found", data: { included: false } },
    kanalregister: { status: "skipped", data: {} },
    journal_site: { status: "skipped", data: {} },
  };
  if (rec.sjr || !rec.sjrUnknown) sources.sjr = sjrSource(rec.sjr, { version: rec.sjr && rec.sjr.year ? `SJR ${rec.sjr.year}` : "SJR" });
  const apc = {};
  if (rec.openalex) apc.openalex = { apc_usd: rec.openalex.apc_usd ?? null, prices: [], is_oa: !!rec.openalex.is_oa, url: rec.openalex.id || null };
  if (rec.doaj) apc.doaj = { has_apc: rec.doaj.has_apc ?? null, prices: rec.doaj.prices || [], url: rec.doaj.url || null };
  sources.apc = Object.keys(apc).length ? { status: "found", data: apc } : { status: "skipped", data: {} };
  const report = { query: rec.issns[0] || rec.title, quick: true, from_search: true, created_at: now, candidates: [], sources,
    identity: { title: rec.title, issns: rec.issns, publisher: rec.publisher, country: (rec.openalex && rec.openalex.country) || null,
      homepage: rec.homepage || null } };
  const glance = [];
  const st = sjrTile(sources.sjr);
  if (st) glance.push(st);
  if (sources.crest.status === "found") glance.push({ label: "DHET subsidy", value: sources.crest.data.status, tone: sources.crest.data.eligible ? "good" : "bad", source: "crest" });
  else if (sources.crest.status === "not_found") glance.push({ label: "DHET subsidy", value: "Not on approved list", tone: "bad", source: "crest" });
  if (n) glance.push({ label: "SANLiC APC agreement", value: n.included ? `Covered${n.oa_type ? ` (${n.oa_type})` : ""}` : "Not covered", tone: n.included ? "good" : "warn", source: "sanlic" });
  const t = apcTile(report);
  if (t) glance.push(t);
  report.glance = glance;
  return report;
}

const DEFAULT_VIEW = { sort: "relevance", sjrQ: "", dhet: false, sanlic: false, oa: false, free: false, maxApc: "", hideUnknownApc: false,
  minCited: "", inDoaj: false, inScopus: false, publisher: "", country: "", savedOnly: false };

// hooks: { isSaved(rec) -> bool, onStar(rec) -> Promise } — the star appears when onStar is given.
export function renderDiscover(result, onOpen, view = {}, hooks = {}) {
  for (const [k, v] of Object.entries(DEFAULT_VIEW)) if (!(k in view)) view[k] = v;
  const box = h("div", { class: "discover" });
  const countries = [...new Set(result.results.map((r) => r.openalex && r.openalex.country).filter(Boolean))].sort();
  const keep = (r) => {
    const apc = apcUsd(r);
    const cited = r.openalex && r.openalex.citedness;
    const max = parseFloat(view.maxApc);
    const min = parseFloat(view.minCited);
    const q = qRank(r);
    return (!view.sjrQ || (q != null && q <= Number(view.sjrQ)))
      && (!view.dhet || eligible(r.crest)) && (!view.sanlic || (r.sanlic && r.sanlic.included))
      && (!view.oa || r.doaj || (r.openalex && r.openalex.is_oa))
      && (!view.free || apc === 0)
      && (isNaN(max) || (apc == null ? !view.hideUnknownApc : apc <= max))
      && (isNaN(min) || (cited != null && cited >= min))
      && (!view.inDoaj || r.sources.has("doaj") || (r.openalex && r.openalex.in_doaj))
      && (!view.inScopus || r.sources.has("scopus"))
      && (!view.publisher || (r.publisher || "").toLowerCase().includes(view.publisher.toLowerCase()))
      && (!view.country || (r.openalex && r.openalex.country) === view.country)
      && (!view.savedOnly || (hooks.isSaved && hooks.isSaved(r)));
  };
  const draw = () => {
    const prev = box.querySelector("details.d-more");   // keep the panel as the user left it
    if (prev) view.moreOpen = prev.open;
    const rows = result.results.filter(keep).sort(SORTS[view.sort] || SORTS.relevance);
    const toggle = (key, label) => h("label", { class: "check" },
      h("input", { type: "checkbox", checked: view[key] || null, onchange: (e) => { view[key] = e.target.checked; draw(); } }), label);
    const field = (key, label, attrs) => h("label", { class: "d-field" }, label,
      h("input", { ...attrs, value: view[key], onchange: (e) => { view[key] = e.target.value.trim(); draw(); } }));
    const sort = h("select", { "aria-label": "Sort by", onchange: (e) => { view.sort = e.target.value; draw(); } },
      [["relevance", "Most relevant"], ["sjr", "SJR (highest first)"], ["citedness", "2-yr citedness"], ["h_index", "h-index"], ["apc", "APC (lowest first)"],
        ["title", "Title A–Z"], ...(result.mode === "scope" ? [["papers", "Matching papers"]] : [])]
        .map(([v, l]) => h("option", { value: v, selected: view.sort === v || null }, l)));
    const active = ["free", "maxApc", "minCited", "inDoaj", "inScopus", "publisher", "country", "savedOnly"]
      .filter((k) => view[k] && view[k] !== DEFAULT_VIEW[k]).length;
    const more = h("details", { class: "d-more", open: view.moreOpen || null, ontoggle: (e) => { view.moreOpen = e.target.open; } },
      h("summary", {}, `More filters${active ? ` (${active} on)` : ""}`),
      h("div", { class: "d-more-grid" },
        toggle("free", "Free to publish (no APC)"),
        field("maxApc", "Max APC (USD)", { type: "number", min: "0", step: "100", inputmode: "numeric", placeholder: "any" }),
        toggle("hideUnknownApc", "Hide unknown APC"),
        field("minCited", "Min 2-yr citedness", { type: "number", min: "0", step: "0.5", inputmode: "decimal", placeholder: "any" }),
        toggle("inDoaj", "Listed in DOAJ"),
        result.mode === "scope" && toggle("inScopus", "Found in Scopus"),
        field("publisher", "Publisher contains", { type: "search", placeholder: "e.g. Elsevier" }),
        countries.length > 1 && h("label", { class: "d-field" }, "Country",
          h("select", { onchange: (e) => { view.country = e.target.value; draw(); } },
            h("option", { value: "" }, "Any"), countries.map((c) => h("option", { value: c, selected: view.country === c || null }, c)))),
        hooks.isSaved && toggle("savedOnly", "Saved journals only"),
        h("button", { type: "button", class: "link", onclick: () => { Object.assign(view, DEFAULT_VIEW, { moreOpen: true }); draw(); } }, "Clear filters")));
    const star = (rec) => {
      if (!hooks.onStar) return null;
      const on = hooks.isSaved && hooks.isSaved(rec);
      return h("button", { type: "button", class: `d-star ${on ? "on" : ""}`, title: on ? "Remove from saved journals" : "Save this journal",
        "aria-label": on ? "Remove from saved journals" : "Save this journal", "aria-pressed": on ? "true" : "false",
        onclick: async (e) => { e.stopPropagation(); e.currentTarget.disabled = true; await hooks.onStar(rec); draw(); } }, on ? "★" : "☆");
    };
    box.replaceChildren(...[
      h("div", { class: "d-head" },
        h("h2", {}, `${rows.length}${rows.length !== result.results.length ? ` of ${result.results.length}` : ""} journal${result.results.length === 1 ? "" : "s"} ${result.mode === "scope" ? "publishing on" : "with titles matching"} “${result.query}”`),
        h("div", { class: "d-controls" }, sort,
          h("select", { "aria-label": "SJR quartile", title: "SCImago's SJR quartile", onchange: (e) => { view.sjrQ = e.target.value; draw(); } },
            [["", "Any SJR quartile"], ["1", "SJR Q1 only"], ["2", "SJR Q1–Q2"], ["3", "SJR Q1–Q3"]]
              .map(([v, l]) => h("option", { value: v, selected: view.sjrQ === v || null }, l))),
          toggle("dhet", "DHET eligible"), toggle("sanlic", "SANLiC covered"), toggle("oa", "Open access"))),
      more,
      result.notes.length > 0 && h("p", { class: "note" }, "Not available this time: " + result.notes.join("; ")),
      h("ul", { class: "d-list" }, rows.length ? rows.map((rec) =>
        h("li", { class: "d-row" }, h("button", { type: "button", class: "d-item", onclick: () => onOpen(rec) },
          h("b", {}, rec.title),
          h("small", {}, [rec.publisher, rec.issns[0], rec.openalex && rec.openalex.country].filter(Boolean).join(" · ")),
          h("span", { class: "d-chips" }, chips(rec).map(([t, tone]) => h("span", { class: `tag ${tone}` }, t)),
            [...rec.sources].map((s) => h("span", { class: "tag src" }, SOURCE_LABEL[s]))),
          details(rec, result.mode) && h("span", { class: "d-meta" }, details(rec, result.mode))), star(rec)))
        : [h("li", { class: "note" }, result.results.length ? "No journals match these filters." : "No journals found. Try fewer or broader keywords.")]),
      h("p", { class: "note" }, result.mode === "scope"
        ? "Ranked by how many papers on the topic each journal published in the last five years. Open a journal for its full check; ☆ saves it."
        : "Ranked by how well titles match, across sources. Open a journal for its full check; ☆ saves it."),
    ].filter(Boolean));
  };
  draw();
  return box;
}
