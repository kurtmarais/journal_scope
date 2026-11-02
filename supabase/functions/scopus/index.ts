// Journal Scope: Scopus lookups for the phone app.
//
// The phone can't hold your Elsevier API key safely, so it calls this Edge
// Function, which adds the key (stored as a Supabase secret) and passes the
// request to Elsevier's Serial Title API. Only signed-in users of this project
// can call it (Supabase checks the sign-in token before the function runs).
//
//   GET /functions/v1/scopus?issn=1532-0464   -> one journal, with CiteScore ranks
//   GET /functions/v1/scopus?title=...        -> matching journals (no ranks)
//
// Secrets (Edge Functions -> Secrets): ELSEVIER_API_KEY (required),
// ELSEVIER_INSTTOKEN (optional, from your library, enables CiteScore ranks
// when the request does not come from your university network).

const API = "https://api.elsevier.com/content/serial/title";
const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
};

function reply(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json" } });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: cors });
  const key = Deno.env.get("ELSEVIER_API_KEY") ?? "";
  if (!key) return reply({ error: "ELSEVIER_API_KEY secret is not set in Supabase" }, 500);
  const headers: Record<string, string> = { "X-ELS-APIKey": key, Accept: "application/json" };
  const token = Deno.env.get("ELSEVIER_INSTTOKEN");
  if (token) headers["X-ELS-Insttoken"] = token;

  const q = new URL(req.url).searchParams;
  const issn = (q.get("issn") ?? "").trim();
  const title = (q.get("title") ?? "").trim().slice(0, 200);
  let target: string;
  if (/^\d{4}-?\d{3}[\dXx]$/.test(issn)) target = `${API}/issn/${issn}?view=CITESCORE`;
  else if (title) target = `${API}?title=${encodeURIComponent(title)}&count=25`;
  else return reply({ error: "Give issn or title" }, 400);

  const get = (url: string) => fetch(url, { headers, signal: AbortSignal.timeout(15000) });
  let resp: Response;
  try {
    resp = await get(target);
  } catch {
    return reply({ error: "Elsevier did not answer within 15 seconds" }, 504);
  }
  let limited = false;
  // CITESCORE has the subject ranks; without that entitlement Elsevier answers
  // 400/403, so fall back to ENHANCED, then STANDARD (CiteScore value, no ranks).
  for (const view of ["ENHANCED", "STANDARD"]) {
    if (!target.includes("view=CITESCORE") || (resp.status !== 400 && resp.status !== 403)) break;
    try {
      resp = await get(target.replace("view=CITESCORE", `view=${view}`));
    } catch {
      return reply({ error: "Elsevier did not answer within 15 seconds" }, 504);
    }
    limited = true;
  }
  if (resp.status === 404) return reply({ "serial-metadata-response": { entry: [] } });
  const text = await resp.text();
  if (!resp.ok) return new Response(text || JSON.stringify({ error: `Elsevier ${resp.status}` }), { status: resp.status, headers: { ...cors, "Content-Type": "application/json" } });
  const body = JSON.parse(text);
  if (limited) body._limited = true;
  return reply(body);
});
