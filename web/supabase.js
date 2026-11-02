// Minimal Supabase client (auth + REST) for the Journal Scope phone app.
// No third-party code: talks to the same endpoints as the desktop app.

export class AuthError extends Error {}
export class SyncError extends Error {}

export class Supabase {
  constructor(url, key) {
    this.url = url.replace(/\/+$/, "");
    this.key = key.trim();
  }

  async request(method, path, { body, token, params, headers } = {}) {
    let url = this.url + path;
    if (params) url += "?" + new URLSearchParams(params).toString();
    const h = { apikey: this.key, "Content-Type": "application/json", Accept: "application/json", ...headers };
    if (token) h.Authorization = `Bearer ${token}`;
    let resp;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), path.startsWith("/functions/") ? 30000 : 20000);
    try {
      resp = await fetch(url, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body), signal: ctrl.signal });
    } catch (e) {
      throw new SyncError(ctrl.signal.aborted ? "No answer in time; check your connection and try again" : "Offline or Supabase unreachable");
    } finally {
      clearTimeout(timer);
    }
    const text = await resp.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = null; }
    if (!resp.ok) {
      const msg = (data && (data.error_description || data.msg || data.message || data.error)) || `HTTP ${resp.status}`;
      if (resp.status === 401 || (path.startsWith("/auth/") && resp.status < 500)) throw new AuthError(msg);
      throw new SyncError(msg);
    }
    return data;
  }

  session(data) {
    return {
      access_token: data.access_token,
      refresh_token: data.refresh_token,
      expires_at: Date.now() / 1000 + Number(data.expires_in || 3600),
      user_id: data.user?.id || "",
      email: data.user?.email || "",
    };
  }

  async signIn(email, password) {
    return this.session(await this.request("POST", "/auth/v1/token",
      { body: { email, password }, params: { grant_type: "password" } }));
  }

  async refresh(refreshToken) {
    return this.session(await this.request("POST", "/auth/v1/token",
      { body: { refresh_token: refreshToken }, params: { grant_type: "refresh_token" } }));
  }

  async changesSince(table, cursor, token) {
    const rows = [];
    for (;;) {
      const params = { select: "*", order: "server_updated_at.asc", limit: "1000" };
      if (cursor) params.server_updated_at = `gt.${cursor}`;
      const page = (await this.request("GET", `/rest/v1/${table}`, { token, params })) || [];
      rows.push(...page);
      if (page.length < 1000) return rows;
      cursor = page[page.length - 1].server_updated_at;
    }
  }

  async select(table, params, token) {
    return (await this.request("GET", `/rest/v1/${table}`, { token, params: { select: "*", ...params } })) || [];
  }

  async insert(table, row, token) {
    await this.request("POST", `/rest/v1/${table}`, {
      body: row, token, headers: { Prefer: "return=minimal" },
    });
  }

  async patch(table, uid, fields, token) {
    await this.request("PATCH", `/rest/v1/${table}`, {
      body: { ...fields, updated_at: Date.now() / 1000 }, token,
      params: { uid: `eq.${uid}` }, headers: { Prefer: "return=minimal" },
    });
  }

  async upsert(table, rows, token) {
    for (let i = 0; i < rows.length; i += 500) {
      await this.request("POST", `/rest/v1/${table}`, {
        body: rows.slice(i, i + 500), token, params: { on_conflict: "uid" },
        headers: { Prefer: "resolution=merge-duplicates,return=minimal" },
      });
    }
  }
}
