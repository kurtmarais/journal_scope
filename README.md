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

## Desktop app

Requires Python 3.10+ ([python.org](https://www.python.org/downloads/); on Windows tick "Add python.exe to PATH").

- **Windows:** double-click `install.bat`.
- **macOS / Linux:** run `bash install.sh` in this folder.

The script installs everything and puts a **Journal Scope** icon on your desktop. The icon opens the app in its own window; closing the window stops it. On macOS, the first time, right-click the icon and choose Open. Run the script again after pulling updates.

## Phone

The lookups need Python and Chromium, so they can't run on a phone. Instead, run the server on a computer and add the page to the phone's home screen, where it behaves like an app with its own icon. Two ways:

**A. Your own computer, via Tailscale (recommended: free, private, works away from home).**
1. Install [Tailscale](https://tailscale.com/download) on the computer and the phone, signed in to the same account.
2. On the computer, in this folder: `.venv/bin/uvicorn app.main:app --port 8000` (Windows: `.venv\Scripts\uvicorn app.main:app --port 8000`).
3. In another terminal: `tailscale serve --bg 8000`. It prints an `https://<computer>.<tailnet>.ts.net` address.
4. Open that address on the phone, then Share > Add to Home Screen (iPhone) or menu > Install app (Android).

The computer must be on for the phone to use it. Because lookups come from your home connection, sites block them less than they would a cloud server.

**B. A cloud host (always available).** Deploy the included `Dockerfile` to any container host (e.g. Render, Fly.io, Railway). Set `JOURNAL_SCOPE_KEY` to a long random value, then open `https://<your-app>/?key=<that value>` once on each device; the key is remembered. Expect a small monthly cost, and expect SCImago and some publisher sites to block a data-centre address more often than a home one.

Installing to the home screen needs an HTTPS address, which both options provide.

## Run it from a terminal

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
uvicorn app.main:app
```

Open http://127.0.0.1:8000 and enter an ISSN (most reliable) or a journal title. If a title matches several SCImago journals you pick one. If the journal homepage isn't found, or is wrong, paste its URL into the "Journal website" field.

Reports can be shared as links, e.g. `http://127.0.0.1:8000/?q=1532-0464`. The JSON is at `/api/report?q=...`.

## How lookups work

- SCImago, Kanalregister and publisher sites are loaded in headless Chromium (SCImago and most publishers block plain scripted requests; Kanalregister builds its pages in the browser). CREST and SANLiC are fetched directly.
- SANLiC's "Journals covered by R&P agreements" spreadsheet is downloaded and cached for 7 days in `~/.cache/journal_scope` (override with `JOURNAL_SCOPE_CACHE`).
- Publisher statistics are found by their labels. Known statistics pages are tried first for Elsevier (Journal Insights), Springer, MDPI, Taylor & Francis, Frontiers, BMC and Wiley; other publishers use the homepage only.
- `JOURNAL_SCOPE_CHROMIUM=/path/to/chrome` uses an existing Chromium. `JOURNAL_SCOPE_NO_BROWSER=1` disables the browser (SCImago, Kanalregister and many journal sites will then fail).

## Limitations

- These are scraped pages, not APIs. When a site changes its layout, the matching module in `app/sources/` needs updating; the tests in `tests/` run against saved copies of each page and show what broke.
- SCImago does not publish the Clarivate Journal Impact Factor. Its "citations per document (2 years)" is the comparable figure; the JIF appears only if the journal's own site states it.
- CREST's search does not accept ISSNs, so it is searched by title and then matched on ISSN.
- Kanalregister offers an official API on request (publiseringskanaler@hkdir.no), which would be faster and more stable than rendering the site.

## Tests

```bash
python -m pytest
```
