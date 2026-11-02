"""Reference lists the phone searches directly (no PC needed at lookup time).

About weekly, the desktop app downloads two complete lists and loads them
into the user's Supabase project:

  CREST    DHET-approved master list      -> ref_crest
  SANLiC   R&P agreement title list       -> ref_sanlic

(Scopus/CiteScore comes from Elsevier's API through a Supabase Edge Function.)

Rows are written with a new batch id, then rows from older batches are
deleted, so the phone never sees an empty list mid-refresh.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Callable

from app.lists import CREST_HOME, crest_file_url, parse_crest_xlsx, sanlic_rows  # noqa: F401 (re-exported)
from app.sources import sanlic

REFRESH_EVERY = 7 * 86400
RETRY_AFTER = 86400


# ------------------------------------------------------------------ refresh
def due(settings: dict, now: float | None = None) -> bool:
    now = now or time.time()
    last_ok = settings.get("ref_refreshed_at", 0)
    last_try = settings.get("ref_tried_at", 0)
    return now - last_ok > REFRESH_EVERY and now - last_try > RETRY_AFTER


def refresh(client, token: Callable[[], str], get_bytes: Callable[[str], bytes],
            tmp_dir: Path) -> dict[str, str]:
    """Download and upload both lists. Returns {list: 'ok N rows' | 'failed: why'}."""
    batch = hashlib.sha1(str(time.time()).encode()).hexdigest()[:10]
    results: dict[str, str] = {}
    tmp_dir.mkdir(parents=True, exist_ok=True)

    def load(name: str, table: str, rows: list[dict], version: str, url: str) -> None:
        for r in rows:
            r["batch"] = batch
        client.upsert(table, rows, token(), on_conflict="user_id,key", chunk=1000)
        client.delete(table, {"batch": f"neq.{batch}"}, token())
        client.upsert("ref_lists", [{"key": name, "version": version, "source_url": url, "rows": len(rows),
                                     "refreshed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}],
                      token(), on_conflict="user_id,key")
        results[name] = f"ok, {len(rows)} journals"

    def do_sanlic():
        page = get_bytes(sanlic.PAGE).decode("utf-8", "replace")
        url = sanlic.parse_page_links(page)[0] or sanlic.FALLBACK_LIST
        path = tmp_dir / "sanlic.xlsx"
        path.write_bytes(get_bytes(url))
        load("sanlic", "ref_sanlic", sanlic_rows(path), url.rsplit("/", 1)[-1], url)

    def do_crest():
        url = crest_file_url(get_bytes(CREST_HOME).decode("utf-8", "replace"))
        if not url:
            raise ValueError("download link not found on the CREST page")
        path = tmp_dir / "crest.xlsx"
        path.write_bytes(get_bytes(url))
        load("crest", "ref_crest", parse_crest_xlsx(path), url.rsplit("/", 1)[-1], url)

    steps = [("sanlic", do_sanlic), ("crest", do_crest)]
    for name, fn in steps:
        try:
            fn()
        except Exception as exc:   # one list failing must not stop the others
            results[name] = f"failed: {exc}"
    return results
