# journal_scope

Checks a journal's validity and credibility for publication by pulling, in one report, what you would otherwise look up on five sites:

| Source | What is taken from it |
| --- | --- |
| [SCImago](https://www.scimagojr.com/) | Existence and Scopus coverage, SJR and best quartile, quartile per category, H-index, citations per document (2 years), homepage link, similar journals |
| [CREST ZA Publications Monitor](https://db.crest.sun.ac.za/zapublications/) | DHET subsidy status (eligible / discontinued / delisted / ...) and the stated reason |
| [Kanalregister](https://kanalregister.hkdir.no/en) | Norwegian Register level (2, 1, 0, X), level history, ceased flag, DOAJ indexing |
| Journal website | Days to first decision / decision after review / acceptance / publication, acceptance rate, APC, impact factor as reported by the journal |
| [SANLiC](https://sanlic.ac.za/read-and-publish-agreements/) | Whether a SANLiC read-and-publish agreement covers the journal, OA model, explanation, agreement period |

Values are copied as each source states them, with a link back (and, for the journal site, the sentence each value was read from). Nothing is scored or combined.

It comes in two parts, set up like Sidebar:

1. **Desktop app (Windows, Python + PySide6).** A window with the search and your report history, plus a tray icon. Closing the window keeps it in the tray. It does all lookups.
2. **Phone app (static web app on Cloudflare, added to the iPhone home screen).** Checks journals on its own from SCImago, CREST and SANLiC lists the PC loads into Supabase weekly, and can ask the PC for the full report (adds Kanalregister and the journal's own statistics). Sync goes through your own Supabase project; see [docs/phone-setup.md](docs/phone-setup.md).

## Install on Windows

Needs Python 3.10+ and Git. Run these in PowerShell, one at a time.

```powershell
git clone -b claude/upbeat-cerf-mntinq https://github.com/kurtmarais/journal_scope "$HOME\Apps\journal_scope"
```

```powershell
cd "$HOME\Apps\journal_scope"
```

```powershell
py -m pip install --user -r requirements.txt
```

```powershell
pyw run_journal_scope.py
```

Then open **⚙ Settings** (bottom left of the window), or right-click the tray icon (Windows may tuck it under the **^** by the clock; drag it onto the taskbar to keep it visible):
- **Phone sync…** connects the phone app (see [docs/phone-setup.md](docs/phone-setup.md)).
- **Add desktop shortcut** puts an icon on your real (OneDrive) Desktop.
- **Start with Windows** keeps it in the tray from sign-in, so phone requests are handled.
- **Update and restart** pulls the latest version. It also updates itself when you quit, as Sidebar does.

Pages are loaded in the app's own embedded browser (Qt WebEngine), so nothing else needs installing. Some sites (SCImago, some publishers) occasionally show an "are you a person" check; when that happens a small window opens with the check for you to complete, and the lookup carries on. The site's cookie is kept, so it is rarely needed twice.

Data lives in `%APPDATA%\JournalScope`: `journal_scope.db` (history), `settings.json`, `launch.log`, `error.log`, `update.log`, and `startup-crash.log` if it fails to start.

## How lookups work

- SCImago, Kanalregister and publisher sites are loaded in the app's embedded browser (Kanalregister builds its pages in the browser; SCImago and many publishers refuse plain scripted requests). CREST and SANLiC are fetched directly. A lookup started from the phone while nobody is at the PC fails for a site that asks for a human check; the phone shows the error.
- SANLiC's "Journals covered by R&P agreements" spreadsheet is downloaded and cached for 7 days.
- Publisher statistics are found by their labels. Known statistics pages are tried first for Elsevier (Journal Insights), Springer, MDPI, Taylor & Francis, Frontiers, BMC and Wiley; other publishers use the homepage only. If the homepage found is wrong, paste the journal's URL into "Journal website".

## Limitations

- These are scraped pages, not APIs. When a site changes its layout, the matching module in `app/sources/` needs updating; the tests run against saved copies of each page and show which source broke.
- SCImago does not publish the Clarivate Journal Impact Factor. Its "citations per document (2 years)" is the comparable figure; the JIF appears only if the journal's own site states it.
- CREST's search does not accept ISSNs, so it is searched by title and then matched on ISSN.
- Kanalregister offers an official API on request (publiseringskanaler@hkdir.no), which would be faster and more stable than rendering the site.

## Layout

```
run_journal_scope.py   launcher for pyw / shortcuts (logs start, catches early crashes)
desktop/               PySide6 app: tray, window (Qt WebEngine), sync engine, updater, Windows helpers
app/                   lookup engine (sources/), local web server and UI, SQLite history
web/                   phone app (static); report-view.js and report.css are shared with the desktop UI
supabase/migrations/   tables + row-level security for sync
wrangler.jsonc         Cloudflare deployment of web/
tests/                 pytest: parsers, report, storage, sync (fake Supabase), desktop (offscreen Qt), phone (headless Chromium)
```

## Tests

```powershell
py -m pip install --user -r requirements-dev.txt
```

```powershell
py -m pytest
```
