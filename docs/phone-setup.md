# Phone setup

The phone app shows the reports your PC has produced and lets you ask for new ones. Lookups always run **on the PC**, because they need Python and a browser engine. The phone only reads results and queues requests.

```
 PC (Journal Scope)  <── sync ──>  Supabase (your project)  <── sync ──>  Phone app
 runs the lookups                   reports + requests                (static files on Cloudflare)
```

- **Reports** checked on the PC appear on the phone within about 20 seconds.
- **Checking on the phone:** type a title or ISSN and tap **Check on my PC**. The PC picks the request up within 20 seconds, provided Journal Scope is running there (in the tray counts). The report then opens on the phone. If the PC is off, the request waits until it is next on.
- **Deleting** a report on either device removes it from both.

Journal Scope uses its **own Supabase project** (`journal_scope`), separate from Sidebar's. The steps match the Sidebar phone setup.

## 1. Create the database tables (one time)

1. In Supabase, open the **journal_scope** project, then **SQL Editor**, then **New query**.
2. Paste the contents of [`supabase/migrations/20261103090000_journal_scope.sql`](../supabase/migrations/20261103090000_journal_scope.sql) and click **Run**.

This creates `journal_reports` and `journal_requests` with row-level security. A signed-in account can only see or change its own rows, and signed-out requests get nothing. It is safe to run again.

## 2. Create your account and lock sign-ups (one time)

This project has its own users, so create an account here even if you have one in Sidebar's project. The same email and password can be reused.

1. **Authentication → Users → Add user → Create new user**, with **Auto Confirm User** ticked.
2. **Authentication → Sign In / Providers**: keep **Email** on and turn **off** "Allow new users to sign up".

## 3. Turn on sync on the PC

**Project Settings → API Keys** in the journal_scope project gives the **Project URL** (`https://<something>.supabase.co`) and the **publishable key** (`sb_publishable_…`). Never use the secret or service_role key.

Right-click the tray icon → **Phone sync…**. Enter that URL and key, your email and password, then click **Sign in and sync**.

- The password is used once and not stored. The sign-in token is encrypted with Windows data protection for your account.
- The bottom of the history panel shows **Synced HH:MM**.
- Tick **Start with Windows** in the tray menu so phone requests are handled whenever the PC is on.

## 4. Put the phone app online with Cloudflare (one time)

1. https://dash.cloudflare.com → **Workers & Pages → Create → Import a repository** → choose `journal_scope`.
2. Branch: `claude/upbeat-cerf-mntinq` (or `main` once merged). Leave the build command empty; the deploy command is `npx wrangler deploy` (it reads `wrangler.jsonc`, which serves `web/`).
3. Deploy. You get an address like `https://journal-scope-phone.<you>.workers.dev`. Every push to that branch redeploys it.

Optional: protect the address with **Zero Trust → Access** (allow only your email), as with Sidebar.

## 5. Install it on the phone

1. Open the workers.dev address in Safari.
2. Enter the project URL and publishable key. To skip this step, put them in `web/config.js` and push.
3. Sign in with your Supabase email and password.
4. **Share → Add to Home Screen**.

Home-screen apps keep their own storage, separate from Safari, so sign in again after adding it.

## Privacy summary

| Where | What's there |
|---|---|
| Supabase | Your reports and pending requests, readable only by your account. |
| Cloudflare | Only the app's own files. Reports never pass through it; the phone talks to Supabase directly. |
| Phone | A copy of your reports and the sign-in token, in the app's own storage. **Sign out** removes them. |
| PC | Report history (`%APPDATA%\JournalScope\journal_scope.db`), settings and logs. |

The phone app loads no third-party scripts, fonts or trackers, and its security headers (`web/_headers`) only allow connections to Supabase.

## If you ran the setup in Sidebar's project earlier

An earlier version used Sidebar's Supabase project. If you ran the Journal Scope SQL there, remove it: in **Sidebar's** project, **SQL Editor**, run the following. It only touches Journal Scope's tables; Sidebar's `boards` and `cards` are unaffected.

```sql
drop table if exists public.journal_reports;
drop table if exists public.journal_requests;
drop function if exists public.journal_scope_newest_wins();
```

If the desktop app was already signed in to Sidebar's project: tray → **Phone sync…** → **Sign out**, then sign in with the journal_scope project's URL and key. On the phone, **Sign out**, tap **Change project** on the sign-in screen, and enter the journal_scope project's URL and key.
