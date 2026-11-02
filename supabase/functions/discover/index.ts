// Journal Scope: "Find journals" for the phone app.
//
// Pass-through calls to OpenAlex, DOAJ and Scopus that add your API keys (kept
// as Supabase secrets). Merging and ranking happen in the app (web/discover.js).
// Mirrors app/discover.py in the desktop app.
//
//   GET /functions/v1/discover?source=openalex&mode=title|scope&q=...
//   GET /functions/v1/discover?source=doaj&mode=title|scope&q=...
//   GET /functions/v1/discover?source=scopus&q=...                  (scope only)
//
// Secrets: ELSEVIER_API_KEY (for Scopus), optional ELSEVIER_INSTTOKEN,
// optional OPENALEX_API_KEY (own daily allowance instead of a shared one).

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
};
const YEARS = 5;

function reply(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json" } });
}

function clean(q: string): string {
  return q.replace(/[^\p{L}\p{N}\s\-']/gu, " ").replace(/\s+/g, " ").trim().slice(0, 200);
}

async function getJSON(url: string, headers: Record<string, string> = {}): Promise<any> {
  const resp = await fetch(url, { headers: { Accept: "application/json", ...headers }, signal: AbortSignal.timeout(20000) });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

async function openalex(mode: string, q: string): Promise<unknown> {
  const key = Deno.env.get("OPENALEX_API_KEY");
  const extra: Record<string, string> = key ? { api_key: key } : {};
  if (mode === "title") {
    const p = new URLSearchParams({ search: q, filter: "type:journal", "per-page": "50", ...extra });
    return { sources: (await getJSON(`https://api.openalex.org/sources?${p}`)).results ?? [] };
  }
  const since = `${new Date().getFullYear() - YEARS}-01-01`;
  const p = new URLSearchParams({ search: q, filter: `primary_location.source.type:journal,from_publication_date:${since}`,
    group_by: "primary_location.source.id", "per-page": "50", ...extra });
  const groups = ((await getJSON(`https://api.openalex.org/works?${p}`)).group_by ?? [])
    .filter((g: any) => g.key && g.key !== "unknown").slice(0, 40);
  const ids = groups.map((g: any) => String(g.key).split("/").pop()).join("|");
  let sources: unknown[] = [];
  if (ids) {
    const p2 = new URLSearchParams({ filter: `openalex:${ids}`, "per-page": "50", ...extra });
    sources = (await getJSON(`https://api.openalex.org/sources?${p2}`)).results ?? [];
  }
  return { groups, sources, since };
}

async function doaj(mode: string, q: string): Promise<unknown> {
  const words = q.split(" ").filter(Boolean);
  if (!words.length) return { results: [] };
  const t = words.join(" AND ");
  const query = mode === "title"
    ? `bibjson.title:(${t})`
    : `bibjson.keywords:(${t}) OR bibjson.subject.term:(${t}) OR bibjson.title:(${t})`;
  const data = await getJSON(`https://doaj.org/api/search/journals/${encodeURIComponent(query)}?pageSize=50`);
  return { results: data.results ?? [] };
}

async function scopus(q: string): Promise<unknown> {
  const key = Deno.env.get("ELSEVIER_API_KEY");
  if (!key) throw new Error("ELSEVIER_API_KEY secret is not set");
  const headers: Record<string, string> = { "X-ELS-APIKey": key };
  const token = Deno.env.get("ELSEVIER_INSTTOKEN");
  if (token) headers["X-ELS-Insttoken"] = token;
  const year = new Date().getFullYear() - YEARS - 1;
  const p = new URLSearchParams({ query: `TITLE-ABS-KEY(${q}) AND PUBYEAR > ${year} AND SRCTYPE(j)`, count: "1",
    facets: "exactsrctitle(count=50,sort=fd)" });
  try {
    return await getJSON(`https://api.elsevier.com/content/search/scopus?${p}`, headers);
  } catch (e) {
    if (/HTTP 40[13]/.test(String(e))) throw new Error("Scopus search needs your institution's access (an institution token)");
    throw e;
  }
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: cors });
  const u = new URL(req.url).searchParams;
  const source = u.get("source") ?? "";
  const mode = u.get("mode") === "scope" ? "scope" : "title";
  const q = clean(u.get("q") ?? "");
  if (q.length < 2) return reply({ error: "Give at least one keyword" }, 400);
  try {
    if (source === "openalex") return reply(await openalex(mode, q));
    if (source === "doaj") return reply(await doaj(mode, q));
    if (source === "scopus") return reply(await scopus(q));
    return reply({ error: "unknown source" }, 404);
  } catch (e) {
    return reply({ error: e instanceof Error ? e.message : String(e) }, 502);
  }
});
