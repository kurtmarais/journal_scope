// Journal Scope phone app.
// Reads reports your PC has produced (synced through your Supabase project) and
// queues new lookups, which the PC runs the next time it syncs.
import { SUPABASE_KEY, SUPABASE_URL } from "./config.js";
import { h, normaliseIssn, normaliseTitle, parseScopusEntry, quickReport, renderCandidates, renderReport, scopusCandidates, scopusEntries } from "./report-view.js";
import { AuthError, Supabase, SyncError } from "./supabase.js";
import { discover, renderDiscover } from "./discover.js";

const APP_VERSION = "2026.11.4";   // shown at the bottom of the home screen; bump with each phone change
const root = document.getElementById("root");
const LS = {
  get: (k, d = null) => { try { const v = localStorage.getItem("js." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem("js." + k, JSON.stringify(v)); } catch { /* storage full or blocked */ } },
  del: (k) => { try { localStorage.removeItem("js." + k); } catch { /* ignore */ } },
};

const state = {
  conf: { url: SUPABASE_URL || LS.get("url", ""), key: SUPABASE_KEY || LS.get("key", "") },
  session: LS.get("session"),
  reports: LS.get("reports", {}),     // uid -> row (without deleted ones)
  requests: LS.get("requests", {}),   // uid -> row
  cursors: LS.get("cursors", {}),
  view: { name: "home" },
  filter: "",
  syncing: false,
  lastSync: null,
  error: "",
};

const sb = () => new Supabase(state.conf.url, state.conf.key);
const uid = () => (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "") : Date.now().toString(16) + Math.random().toString(16).slice(2));
const now = () => Date.now() / 1000;
const dateFmt = (t) => new Date(t * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
const timeFmt = (d) => d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

function save() {
  LS.set("reports", state.reports);
  LS.set("requests", state.requests);
  LS.set("cursors", state.cursors);
}

// ------------------------------------------------------------------ auth
async function token() {
  const s = state.session;
  if (!s) throw new AuthError("Not signed in");
  if (s.expires_at - now() > 120) return s.access_token;
  state.session = await sb().refresh(s.refresh_token);
  LS.set("session", state.session);
  return state.session.access_token;
}

function signOut() {
  for (const k of ["session", "reports", "requests", "cursors"]) LS.del(k);
  Object.assign(state, { session: null, reports: {}, requests: {}, cursors: {}, view: { name: "home" } });
  render();
}

// ------------------------------------------------------------------ sync
async function sync() {
  if (state.syncing || !state.session) return;
  state.syncing = true;
  try {
    const t = await token();
    for (const table of ["journal_reports", "journal_requests"]) {
      const rows = await sb().changesSince(table, state.cursors[table] || "", t);
      const store = table === "journal_reports" ? state.reports : state.requests;
      for (const row of rows) {
        if (row.deleted) delete store[row.uid];
        else if (!store[row.uid] || store[row.uid].updated_at <= row.updated_at) store[row.uid] = row;
        state.cursors[table] = row.server_updated_at;
      }
    }
    // A finished lookup: its report is now in the list, so the request can go.
    for (const r of Object.values(state.requests)) {
      if (r.status === "done" && r.report_uid && state.reports[r.report_uid]) {
        await sb().patch("journal_requests", r.uid, { deleted: true }, t);
        delete state.requests[r.uid];
        if (state.view.name === "waiting" && state.view.request === r.uid) state.view = { name: "report", uid: r.report_uid };
      }
    }
    save();
    state.error = "";
    state.lastSync = new Date();
  } catch (e) {
    if (e instanceof AuthError) { state.error = "Signed out: " + e.message; state.session = null; LS.del("session"); }
    else state.error = e instanceof SyncError ? e.message : String(e);
  } finally {
    state.syncing = false;
    render();
    schedule();
  }
}

let timer = null;
function schedule() {
  clearTimeout(timer);
  const busy = Object.values(state.requests).some((r) => r.status === "pending" || r.status === "running");
  if (document.visibilityState === "visible" && state.session) timer = setTimeout(sync, busy ? 6000 : 60000);
}
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") sync(); else clearTimeout(timer); });

async function requestLookup(query, site, sid = null) {
  const row = { uid: uid(), query, site: site || null, sid, status: "pending", created_at: now(), updated_at: now() };
  try {
    await sb().insert("journal_requests", row, await token());
    state.requests[row.uid] = row;
    save();
    state.view = { name: "waiting", request: row.uid };
  } catch (e) {
    state.error = "Could not send the request: " + e.message;
  }
  render();
  sync();
}

async function dismissRequest(r) {
  delete state.requests[r.uid];
  save();
  render();
  try { await sb().patch("journal_requests", r.uid, { deleted: true }, await token()); } catch { /* retried on next delete */ }
}

async function deleteReport(id) {
  if (!confirm("Delete this report? It is removed from the PC as well.")) return;
  try {
    await sb().patch("journal_reports", id, { deleted: true, report: {} }, await token());
    delete state.reports[id];
    save();
    state.view = { name: "home" };
  } catch (e) {
    state.error = "Could not delete: " + e.message;
  }
  render();
}

// ---------------------------------------------------------- phone search
// Searches the lists the PC loads into Supabase. Runs entirely from the phone.
async function scopusLookup(api, t, params) {
  // Edge Function "scopus" calls Elsevier's Serial Title API with the key stored in Supabase.
  return api.request("GET", "/functions/v1/scopus", { token: t, params });
}

async function quickSearch(query, site, sourceId = null) {
  state.view = { name: "quick", query, site, loading: true };
  render();
  try {
    const t = await token();
    const api = sb();
    const issn = sourceId || normaliseIssn(query);
    const lists = await api.select("ref_lists", {}, t);
    let scopus = null, scopusNote = null, scopusError = null;
    try {
      if (issn) {
        const payload = await scopusLookup(api, t, { issn });
        const entry = scopusEntries(payload)[0];
        scopus = entry ? parseScopusEntry(entry, !!payload._limited) : null;
        if (payload._limited) scopusNote = "CiteScore subject ranks need institutional access, which this lookup did not have.";
      } else {
        const found = scopusCandidates(scopusEntries(await scopusLookup(api, t, { title: query })));
        const exact = found.filter((c) => normaliseTitle(c.title) === normaliseTitle(query));
        const pick = found.length === 1 ? found[0] : exact.length === 1 ? exact[0] : null;
        if (!pick && found.length > 1) {
          state.view = { name: "quick", query, site, candidates: found };
          render();
          return;
        }
        if (pick) return quickSearch(query, site, pick.source_id);
      }
    } catch (e) {
      scopusError = /404|not found/i.test(e.message)
        ? "The Scopus lookup isn't set up yet: deploy the 'scopus' Edge Function (docs/phone-setup.md)."
        : "Scopus lookup failed: " + e.message;
    }
    const issns = scopus ? scopus.issns : issn ? [issn] : [];
    const title = scopus ? scopus.title : issn ? null : query;
    const inList = `(${issns.join(",")})`;
    const [crestRows, sanlicRows] = await Promise.all([
      issns.length ? api.select("ref_crest", { or: `(issn.in.${inList},eissn.in.${inList})`, limit: "1" }, t) : Promise.resolve([]),
      issns.length ? api.select("ref_sanlic", { issns: `ov.{${issns.join(",")}}`, limit: "1" }, t) : Promise.resolve([]),
    ]);
    const norm = normaliseTitle(title);
    const crest = crestRows[0] || (norm ? (await api.select("ref_crest", { title_norm: `eq.${norm}`, limit: "1" }, t))[0] : null) || null;
    const sanlic = sanlicRows[0] || (norm ? (await api.select("ref_sanlic", { title_norm: `eq.${norm}`, limit: "1" }, t))[0] : null) || null;
    const report = quickReport(query, { scopus, scopusNote, scopusError, crest, sanlic, lists, issns, title });
    state.view = { name: "quick", query, site, report, sid: issns[0] || null, listsLoaded: lists.length > 0 };
  } catch (e) {
    state.view = { name: "quick", query, site, error: e.message };
  }
  render();
}

// ---------------------------------------------------------- find journals
function phoneProvider(api, t) {
  const fn = (params) => api.request("GET", "/functions/v1/discover", { token: t, params });
  const chunks = (arr, n) => Array.from({ length: Math.ceil(arr.length / n) }, (_, i) => arr.slice(i * n, i * n + n));
  return {
    openalex: (mode, q) => fn({ source: "openalex", mode, q }),
    doaj: (mode, q) => fn({ source: "doaj", mode, q }),
    scopus: (q) => fn({ source: "scopus", mode: "scope", q }),
    async crestSearch(q) {
      const words = normaliseTitle(q).split(" ").filter((w) => w.length > 1);
      if (!words.length) return { results: [] };
      const rows = await api.select("ref_crest", { and: `(${words.map((w) => `title_norm.ilike.*${w}*`).join(",")})`, limit: "50" }, t);
      return { results: rows };
    },
    async refmatch(issns, titles) {
      const crest = [], sanlic = [];
      for (const part of chunks(issns, 40)) {
        const list = `(${part.join(",")})`;
        crest.push(...await api.select("ref_crest", { or: `(issn.in.${list},eissn.in.${list})` }, t));
        sanlic.push(...await api.select("ref_sanlic", { issns: `ov.{${part.join(",")}}` }, t));
      }
      const names = titles.map(normaliseTitle).filter(Boolean);
      for (const part of chunks(names, 20)) {
        const list = `(${part.map((n) => `"${n}"`).join(",")})`;
        crest.push(...await api.select("ref_crest", { title_norm: `in.${list}` }, t));
        sanlic.push(...await api.select("ref_sanlic", { title_norm: `in.${list}` }, t));
      }
      const lists = await api.select("ref_lists", {}, t);
      const errors = {};
      for (const k of ["crest", "sanlic"]) if (!lists.some((l) => l.key === k)) errors[k] = "list not loaded yet";
      return { crest, sanlic, errors };
    },
  };
}

async function findJournals(query, mode) {
  state.view = { name: "discover", query, mode, loading: true };
  render();
  try {
    const t = await token();
    const result = await discover(mode, query, phoneProvider(sb(), t));
    state.view = { name: "discover", query, mode, result, opts: { sort: "relevance", dhet: false, sanlic: false, oa: false } };
  } catch (e) {
    state.view = { name: "discover", query, mode, error: e.message };
  }
  render();
}

function discoverScreen(v) {
  if (v.loading) return h("div", {}, backBar(), h("p", { class: "note" },
    v.mode === "scope" ? `Finding journals that publish on “${v.query}”…` : `Finding journals with “${v.query}” in their titles…`));
  if (v.error) return h("div", {}, backBar(), h("p", { class: "err" }, "Search failed: " + v.error));
  return h("div", {}, backBar(), renderDiscover(v.result, (rec) => {
    state.back = v;                       // ‹ Back returns to these results
    quickSearch(rec.issns[0] || rec.title, "");
  }, v.opts));
}

function fullReportFor(issns) {
  if (!issns || !issns.length) return null;
  return Object.values(state.reports).filter((r) => (r.issns || []).some((i) => issns.includes(i)))
    .sort((a, b) => b.created_at - a.created_at)[0] || null;
}

// ---------------------------------------------------------------- screens
function setupScreen() {
  const url = h("input", { type: "url", placeholder: "https://<project>.supabase.co", value: state.conf.url });
  const key = h("input", { placeholder: "sb_publishable_…", value: state.conf.key });
  return h("div", { class: "narrow" },
    h("h1", {}, "Journal Scope"),
    h("p", { class: "note" }, "Enter your Supabase project URL and publishable key (Project Settings → API Keys)."),
    h("form", { onsubmit: (e) => {
      e.preventDefault();
      state.conf = { url: url.value.trim(), key: key.value.trim() };
      LS.set("url", state.conf.url); LS.set("key", state.conf.key);
      render();
    } }, h("label", {}, "Project URL", url), h("label", {}, "Publishable key", key), h("button", { class: "primary" }, "Continue")));
}

function signInScreen() {
  const email = h("input", { type: "email", autocomplete: "username", required: true });
  const pw = h("input", { type: "password", autocomplete: "current-password", required: true });
  const msg = h("p", { class: "err" }, state.error);
  return h("div", { class: "narrow" },
    h("h1", {}, "Journal Scope"),
    h("p", { class: "note" }, "Sign in with the account you use for phone sync on the PC."),
    h("form", { onsubmit: async (e) => {
      e.preventDefault();
      msg.textContent = "Signing in…";
      try {
        state.session = await sb().signIn(email.value.trim(), pw.value);
        LS.set("session", state.session);
        state.error = "";
        render();
        sync();
      } catch (err) { msg.textContent = err.message; }
    } }, h("label", {}, "Email", email), h("label", {}, "Password", pw), h("button", { class: "primary" }, "Sign in"), msg),
    !SUPABASE_URL && h("button", { class: "link", onclick: () => { state.conf = { url: "", key: "" }; LS.del("url"); LS.del("key"); render(); } }, "Change project"));
}

const TONES = { good: "good", warn: "warn", bad: "bad" };
function miniChips(glance) {
  const pick = ["DHET subsidy", "Norwegian Register", "SANLiC APC agreement"];
  const items = (glance || []).filter((g) => pick.includes(g.label) || g.label.startsWith("CiteScore"));
  return h("div", { class: "mini" }, items.map((g) => h("span", { class: `tag ${TONES[g.tone] || "neutral"}` },
    g.label.startsWith("CiteScore") ? "CiteScore " + g.value.replace(/^([\d.]+)(?: \((Q\d).*)?$/, (m, v, q) => q ? `${v} ${q}` : v) :
    g.label.startsWith("SJR") ? g.value.replace(/^.*\((Q\d)\).*$/, "$1") : g.label === "DHET subsidy" ? `DHET: ${g.value}` : g.value)));
}

function requestRow(r) {
  let text, actions = [];
  const age = now() - r.created_at;
  if (r.status === "pending") text = age > 90 ? "Waiting for your PC (Journal Scope must be running there)" : "Waiting for your PC…";
  else if (r.status === "running") text = "Your PC is checking it…";
  else if (r.status === "error") { text = "Failed: " + (r.error || "unknown error"); actions = [h("button", { class: "link", onclick: () => dismissRequest(r) }, "Dismiss")]; }
  else if (r.status === "done" && r.candidates) {
    text = "Several journals match.";
    actions = [h("button", { class: "link", onclick: () => { state.view = { name: "candidates", request: r.uid }; render(); } }, "Choose"),
      h("button", { class: "link", onclick: () => dismissRequest(r) }, "Dismiss")];
  } else text = "Finished; fetching the report…";
  return h("li", { class: `req ${r.status}` }, h("b", {}, r.query), h("span", {}, text), actions.length && h("div", { class: "acts" }, actions));
}

function homeScreen() {
  const q = h("input", { name: "q", placeholder: "Journal title or ISSN", required: true, enterkeyhint: "search", autocapitalize: "off" });
  const site = h("input", { name: "site", type: "url", placeholder: "Journal website (optional)", autocapitalize: "off" });
  const reqs = Object.values(state.requests).sort((a, b) => b.created_at - a.created_at);
  let reports = Object.values(state.reports).sort((a, b) => b.created_at - a.created_at);
  const filterBox = reports.length > 8 && h("input", { class: "filter", type: "search", placeholder: "Filter reports", value: state.filter,
    oninput: (e) => { state.filter = e.target.value; renderList(); } });
  const list = h("ul", { class: "reports" });
  function renderList() {
    const f = state.filter.trim().toLowerCase();
    const shown = f ? reports.filter((r) => (r.title + " " + r.query + " " + (r.issns || []).join(" ")).toLowerCase().includes(f)) : reports;
    list.replaceChildren(...(shown.length ? shown.map((r) => h("li", {},
      h("button", { type: "button", onclick: () => { state.view = { name: "report", uid: r.uid }; render(); window.scrollTo(0, 0); } },
        h("b", {}, r.title || r.query),
        h("small", {}, [(r.issns || [])[0], dateFmt(r.created_at)].filter(Boolean).join(" · ")),
        miniChips(r.report && r.report.glance)))) : [h("li", { class: "empty" }, f ? "No reports match." : "Reports from your PC appear here.")]));
  }
  renderList();
  return h("div", {},
    h("header", { class: "bar" }, h("h1", {}, "Journal Scope"), h("button", { class: "link", onclick: () => confirm("Sign out? Reports stay on the PC and in Supabase.") && signOut() }, "Sign out")),
    h("form", { class: "ask", onsubmit: (e) => { e.preventDefault(); state.back = null; quickSearch(q.value.trim(), site.value.trim()); } },
      q, site, h("button", { class: "primary" }, "Check journal")),
    findForm(),
    reqs.length > 0 && h("ul", { class: "requests" }, reqs.map(requestRow)),
    filterBox, list,
    h("p", { class: "foot" }, state.error ? h("span", { class: "err" }, state.error) : state.lastSync ? `Synced ${timeFmt(state.lastSync)}` : "Syncing…",
      ` · version ${APP_VERSION}`));
}

function findForm() {
  const fq = h("input", { name: "fq", placeholder: "Find journals by keyword", required: true, minlength: "2", enterkeyhint: "search", autocapitalize: "off" });
  const mode = h("select", { name: "fmode", class: "mode" },
    h("option", { value: "title" }, "In titles (OpenAlex, DOAJ, CREST)"),
    h("option", { value: "scope" }, "By scope: what they publish (OpenAlex, Scopus, DOAJ)"));
  return h("form", { class: "ask find", onsubmit: (e) => { e.preventDefault(); findJournals(fq.value.trim(), mode.value); } },
    fq, mode, h("button", { class: "secondary" }, "Find journals"));
}

function backBar(title) {
  const back = () => {
    const prev = state.back;
    state.back = null;
    state.view = prev && state.view.name !== "discover" ? prev : { name: "home" };
    render();
  };
  return h("header", { class: "bar" }, h("button", { class: "link back", onclick: back }, "‹ Back"), h("span", { class: "bar-title" }, title || ""));
}

function reportScreen(id) {
  const row = state.reports[id];
  if (!row) { state.view = { name: "home" }; return homeScreen(); }
  return h("div", {}, backBar(),
    renderReport(row.report),
    h("p", { class: "foot" }, `Checked ${dateFmt(row.created_at)} on your PC.`),
    h("button", { class: "danger", onclick: () => deleteReport(id) }, "Delete report"));
}

function waitingScreen(rid) {
  const r = state.requests[rid];
  if (!r) { state.view = { name: "home" }; return homeScreen(); }
  if (r.status === "done" && r.candidates) { state.view = { name: "candidates", request: rid }; return candidatesScreen(rid); }
  return h("div", {}, backBar(), h("ul", { class: "requests" }, requestRow(r)),
    h("p", { class: "note" }, "Lookups run on your PC, which checks for new requests every 20 seconds while Journal Scope is running. You can leave this screen; the report appears in the list."));
}

function candidatesScreen(rid) {
  const r = state.requests[rid];
  if (!r) { state.view = { name: "home" }; return homeScreen(); }
  return h("div", {}, backBar(), renderCandidates(r.query, r.candidates || [], async (c) => {
    await dismissRequest(r);
    requestLookup(r.query, r.site, c.source_id);
  }));
}

function quickScreen(v) {
  if (v.loading) return h("div", {}, backBar(), h("p", { class: "note" }, `Searching for “${v.query}”…`));
  if (v.error) return h("div", {}, backBar(), h("p", { class: "err" }, "Search failed: " + v.error),
    h("button", { class: "primary wide", onclick: () => requestLookup(v.query, v.site) }, "Check on my PC instead"));
  if (v.candidates) return h("div", {}, backBar(), renderCandidates(v.query, v.candidates, (c) => quickSearch(v.query, v.site, c.source_id)));
  const full = fullReportFor(v.report.identity.issns);
  return h("div", {}, backBar(),
    !v.listsLoaded && h("p", { class: "err" }, "The phone lists haven't been loaded yet. On the PC: Settings → Phone sync…, then Settings → Refresh phone lists now."),
    h("div", { class: "actions" },
      full && h("button", { class: "secondary", onclick: () => { state.view = { name: "report", uid: full.uid }; render(); window.scrollTo(0, 0); } },
        `Open full report (${dateFmt(full.created_at)})`),
      h("button", { class: "secondary", onclick: () => requestLookup(v.query, v.site, v.sid) },
        full ? "Refresh full report on my PC" : "Get full report from my PC")),
    h("p", { class: "note" }, "Instant check from Scopus and the CREST and SANLiC lists. The full report adds Kanalregister and the journal's own statistics; your PC runs it when it is on."),
    renderReport(v.report));
}

function render() {
  let screen;
  if (!state.conf.url || !state.conf.key) screen = setupScreen();
  else if (!state.session) screen = signInScreen();
  else if (state.view.name === "report") screen = reportScreen(state.view.uid);
  else if (state.view.name === "waiting") screen = waitingScreen(state.view.request);
  else if (state.view.name === "candidates") screen = candidatesScreen(state.view.request);
  else if (state.view.name === "quick") screen = quickScreen(state.view);
  else if (state.view.name === "discover") screen = discoverScreen(state.view);
  else screen = homeScreen();
  const focused = document.activeElement && document.activeElement.name;
  const typed = focused && document.activeElement.value;
  root.replaceChildren(h("main", { class: "app" }, screen));
  if (focused) {   // a background sync must not wipe what is being typed
    const el = root.querySelector(`[name="${focused}"]`);
    if (el) { el.value = typed; el.focus(); }
  }
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
render();
sync();
