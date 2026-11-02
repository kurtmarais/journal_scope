// "Find journals": keyword search across OpenAlex, DOAJ, CREST and Scopus.
// Shared by the desktop window and the phone app; each passes a `provider` that
// knows how to reach the services (desktop: its local server; phone: Supabase).
//
//   title mode : journals whose titles contain the keywords   (OpenAlex, DOAJ, CREST)
//   scope mode : journals that publish the most on the topic  (OpenAlex papers, Scopus papers, DOAJ keywords/subjects)
//
// Every result is matched against the CREST (DHET subsidy) and SANLiC lists.
import { h, normaliseIssn, normaliseTitle } from "./report-view.js";

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
      url: (b.ref || {}).journal || null },
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
      for (const i of [rec.crest && rec.crest.issn, rec.crest && rec.crest.eissn].filter(Boolean)) if (!rec.issns.includes(i)) rec.issns.push(i);
    }
    if (Object.keys((match && match.errors) || {}).length) for (const rec of records) rec.listsUnknown = true;
  } catch (e) {
    notes.push(`DHET/SANLiC lists: ${e.message}`);
    for (const rec of records) rec.listsUnknown = true;
  }
  return { mode, query, results: records, notes };
}

// ------------------------------------------------------------- display
const eligible = (c) => !!c && /eligible for subsidy|^active$/i.test(c.status || "");

function chips(rec) {
  const out = [];
  if (rec.listsUnknown && !rec.crest) out.push(["DHET: unknown", "neutral"]);
  else if (!rec.crest) out.push(["Not on DHET list", "bad"]);
  else out.push([eligible(rec.crest) ? "DHET eligible" : `DHET: ${rec.crest.status}`, eligible(rec.crest) ? "good" : "bad"]);
  if (rec.sanlic) out.push([rec.sanlic.included ? `SANLiC covered${rec.sanlic.oa_type ? ` (${rec.sanlic.oa_type})` : ""}` : "SANLiC: not covered", rec.sanlic.included ? "good" : "warn"]);
  if (rec.doaj && rec.doaj.apc) out.push([`OA · ${rec.doaj.apc}`, "neutral"]);
  else if (rec.openalex && rec.openalex.is_oa) out.push([`OA${rec.openalex.apc_usd ? ` · USD ${rec.openalex.apc_usd.toLocaleString()}` : ""}`, "neutral"]);
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
  citedness: (a, b) => ((b.openalex && b.openalex.citedness) || -1) - ((a.openalex && a.openalex.citedness) || -1),
  papers: (a, b) => ((b.counts.openalex || 0) + (b.counts.scopus || 0)) - ((a.counts.openalex || 0) + (a.counts.scopus || 0)),
};

export function renderDiscover(result, onOpen, view = { sort: "relevance", dhet: false, sanlic: false, oa: false }) {
  const box = h("div", { class: "discover" });
  const draw = () => {
    let rows = result.results.filter((r) => (!view.dhet || eligible(r.crest)) && (!view.sanlic || (r.sanlic && r.sanlic.included))
      && (!view.oa || (r.doaj || (r.openalex && r.openalex.is_oa))));
    rows = [...rows].sort(SORTS[view.sort] || SORTS.relevance);
    const toggle = (key, label) => h("label", { class: "check" },
      h("input", { type: "checkbox", checked: view[key] || null, onchange: (e) => { view[key] = e.target.checked; draw(); } }), label);
    const sort = h("select", { onchange: (e) => { view.sort = e.target.value; draw(); } },
      [["relevance", "Most relevant"], ["citedness", "2-yr citedness"], ...(result.mode === "scope" ? [["papers", "Matching papers"]] : [])]
        .map(([v, l]) => h("option", { value: v, selected: view.sort === v || null }, l)));
    box.replaceChildren(...[
      h("div", { class: "d-head" },
        h("h2", {}, `${rows.length} journal${rows.length === 1 ? "" : "s"} ${result.mode === "scope" ? "publishing on" : "with titles matching"} “${result.query}”`),
        h("div", { class: "d-controls" }, sort, toggle("dhet", "DHET eligible"), toggle("sanlic", "SANLiC covered"), toggle("oa", "Open access"))),
      result.notes.length > 0 && h("p", { class: "note" }, "Not available this time: " + result.notes.join("; ")),
      h("ul", { class: "d-list" }, rows.length ? rows.map((rec) =>
        h("li", {}, h("button", { type: "button", class: "d-item", onclick: () => onOpen(rec) },
          h("b", {}, rec.title),
          h("small", {}, [rec.publisher, rec.issns[0]].filter(Boolean).join(" · ")),
          h("span", { class: "d-chips" }, chips(rec).map(([t, tone]) => h("span", { class: `tag ${tone}` }, t)),
            [...rec.sources].map((s) => h("span", { class: "tag src" }, SOURCE_LABEL[s]))),
          details(rec, result.mode) && h("span", { class: "d-meta" }, details(rec, result.mode)))))
        : [h("li", { class: "note" }, "No journals found. Try fewer or broader keywords.")]),
      h("p", { class: "note" }, result.mode === "scope"
        ? "Ranked by how many papers on the topic each journal published in the last five years. Open a journal for its full check."
        : "Ranked by how well titles match, across sources. Open a journal for its full check."),
    ].filter(Boolean));
  };
  draw();
  return box;
}
