"""Phone app end to end: headless Chromium at phone size, strict CSP, fake Supabase,
and the real desktop sync engine playing the PC."""

import asyncio
import functools
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.storage import Database
from desktop.sync import SyncEngine
from tests.fake_supabase import EMAIL, PASSWORD, FakeSupabase
from tests.test_report import FakeFetcher, make_xlsx

pw = pytest.importorskip("playwright.async_api")
WEB = Path(__file__).resolve().parent.parent / "web"
CHROME = os.environ.get("JOURNAL_SCOPE_CHROMIUM") or next(
    (str(p) for p in Path("/opt/pw-browsers").glob("chromium-*/chrome-linux*/chrome")), None)
SHOTS = os.environ.get("JOURNAL_SCOPE_SHOTS")


def serve_web(supabase_url: str):
    csp = ("default-src 'self'; connect-src 'self' " + supabase_url + "; img-src 'self' data:; style-src 'self'; "
           "script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def end_headers(self):
            self.send_header("Content-Security-Policy", csp)
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def do_GET(self):
            if self.path.split("?")[0] == "/config.js":
                body = (f'export const SUPABASE_URL = "{supabase_url}";\n'
                        'export const SUPABASE_KEY = "sb_publishable_test";\n').encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(WEB)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


def test_phone_request_report_delete(tmp_path):
    fake = FakeSupabase()
    api, api_url = fake.serve()
    web, web_url = serve_web(api_url)

    # The "PC": real sync engine, lookups answered from saved pages.
    from app.report import build_report
    fetcher = FakeFetcher(make_xlsx(tmp_path / "l.xlsx"))
    db = Database(tmp_path / "pc.db")
    engine = SyncEngine(db, {}, lambda s: None,
                        lambda q, sid, site: asyncio.run(build_report(fetcher, q, sid, site)), opener=fake.opener)
    engine.sign_in(api_url, "sb_publishable_test", EMAIL, PASSWORD)

    async def run():
        async with pw.async_playwright() as p:
            browser = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            ctx = await browser.new_context(viewport={"width": 393, "height": 852}, device_scale_factor=2,
                                            is_mobile=True, has_touch=True)
            page = await ctx.new_page()
            errors = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))

            await page.goto(web_url)
            await page.fill("input[type=email]", EMAIL)
            await page.fill("input[type=password]", PASSWORD)
            await page.click("button:has-text('Sign in')")
            await page.wait_for_selector("text=Reports from your PC appear here.")

            await page.fill("input[name=q]", "1532-0464")
            await page.click("button:has-text('Check journal')")
            await page.click("button:has-text('Get full report from my PC')")
            await page.wait_for_selector("text=Waiting for your PC")
            pending = [r for r in fake.tables["journal_requests"].values() if r["status"] == "pending"]
            assert [r["query"] for r in pending] == ["1532-0464"]

            await asyncio.to_thread(engine.cycle)          # the PC picks it up
            await page.wait_for_selector("h2:has-text('Journal of Biomedical Informatics')", timeout=15000)
            assert await page.is_visible("text=Eligible for subsidy")
            assert await page.is_visible("text=USD 3,550")
            if SHOTS:
                await page.screenshot(path=f"{SHOTS}/phone_report.png", full_page=True)
            width = await page.evaluate("document.documentElement.scrollWidth")
            assert width <= 393, f"horizontal scroll: {width}px"

            await page.click("text=‹ Back")
            await page.wait_for_selector(".reports b:has-text('Journal of Biomedical Informatics')")
            if SHOTS:
                await page.screenshot(path=f"{SHOTS}/phone_home.png")
            # the finished request is cleared once its report has arrived
            assert all(r["deleted"] for r in fake.tables["journal_requests"].values())

            await page.click(".reports button")
            await page.click("text=Delete report")
            await page.wait_for_selector("text=Reports from your PC appear here.")
            await asyncio.to_thread(engine.cycle)
            assert db.list_reports() == []

            assert not errors, errors
            await browser.close()

    try:
        asyncio.run(run())
    finally:
        api.shutdown()
        web.shutdown()


def test_phone_searches_lists_without_pc(tmp_path):
    from tests.reflist_samples import get_bytes_for

    fake = FakeSupabase()
    api, api_url = fake.serve()
    web, web_url = serve_web(api_url)
    # The PC loads the lists once (then may be switched off).
    pc = SyncEngine(Database(tmp_path / "pc.db"), {}, lambda s: None, lambda *a: {}, opener=fake.opener,
                    get_bytes=get_bytes_for(tmp_path), tmp_dir=tmp_path / "lists")
    pc.sign_in(api_url, "sb_publishable_test", EMAIL, PASSWORD)
    assert all(v.startswith("ok") for v in pc.cycle()["lists"].values())

    async def run():
        async with pw.async_playwright() as p:
            browser = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            ctx = await browser.new_context(viewport={"width": 393, "height": 852}, is_mobile=True, has_touch=True)
            page = await ctx.new_page()
            errors = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(web_url)
            await page.fill("input[type=email]", EMAIL)
            await page.fill("input[type=password]", PASSWORD)
            await page.click("button:has-text('Sign in')")
            await page.wait_for_selector("text=Reports from your PC appear here.")

            # ISSN search: instant, from the lists
            await page.fill("input[name=q]", "1532-0480")
            await page.click("button:has-text('Check journal')")
            await page.wait_for_selector("h2:has-text('Journal of Biomedical Informatics')", timeout=10000)
            for text in ("10.8 (Q1, 95th percentile)", "Eligible for subsidy", "Covered (Hybrid)", "Coverage 2001-2026"):
                assert await page.is_visible(f"text={text}"), text
            assert await page.is_visible("article:has(h3:has-text('Kanalregister')) >> text=Not checked")
            guide = page.locator(".templates a", has_text="Author guidelines for this journal")
            assert await guide.get_attribute("href") == \
                "https://www.sciencedirect.com/journal/journal-of-biomedical-informatics/publish/guide-for-authors"
            assert await page.locator(".templates a", has_text="Elsevier templates on Overleaf").count() == 1
            if SHOTS:
                await page.screenshot(path=f"{SHOTS}/phone_quick.png", full_page=True)
            assert await page.evaluate("document.documentElement.scrollWidth") <= 393
            assert not fake.tables["journal_requests"]          # nothing was sent to the PC

            # Optional full report from the PC
            await page.click("button:has-text('Get full report from my PC')")
            await page.wait_for_selector("text=Waiting for your PC")
            req = next(iter(fake.tables["journal_requests"].values()))
            assert req["sid"] == "1532-0464"

            # Title search with several matches -> choose
            await page.click("text=‹ Back")
            await page.fill("input[name=q]", "Journal of Biomedical")
            await page.click("button:has-text('Check journal')")
            await page.wait_for_selector("text=Several journals match")
            await page.click(".candidates button:has-text('Journal of Biomedical Informatics: X')")
            await page.wait_for_selector("text=Not in Scopus")
            assert await page.is_visible(".tile.bad:has-text('Discontinued')")     # CREST list status
            assert await page.is_visible("text=Not covered")

            # Unknown journal
            await page.click("text=‹ Back")
            await page.fill("input[name=q]", "0000-0019")
            await page.click("button:has-text('Check journal')")
            await page.wait_for_selector("text=Not in Scopus")

            assert not errors, errors
            await browser.close()

    try:
        asyncio.run(run())
    finally:
        api.shutdown()
        web.shutdown()


def test_phone_find_journals(tmp_path):
    from tests.reflist_samples import get_bytes_for

    fake = FakeSupabase()
    api, api_url = fake.serve()
    web, web_url = serve_web(api_url)
    pc = SyncEngine(Database(tmp_path / "pc.db"), {}, lambda s: None, lambda *a: {}, opener=fake.opener,
                    get_bytes=get_bytes_for(tmp_path), tmp_dir=tmp_path / "lists")
    pc.sign_in(api_url, "sb_publishable_test", EMAIL, PASSWORD)
    pc.cycle()   # loads the CREST and SANLiC lists

    async def run():
        async with pw.async_playwright() as p:
            browser = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            ctx = await browser.new_context(viewport={"width": 393, "height": 852}, is_mobile=True, has_touch=True)
            page = await ctx.new_page()
            errors = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(web_url)
            await page.fill("input[type=email]", EMAIL)
            await page.fill("input[type=password]", PASSWORD)
            await page.click("button:has-text('Sign in')")
            await page.wait_for_selector("text=Reports from your PC appear here.")

            # Titles mode: OpenAlex + DOAJ + CREST, with DHET/SANLiC status on each
            await page.fill("input[name=fq]", "informatics")
            await page.click("button:has-text('Find journals')")
            await page.wait_for_selector(".d-item")
            jbi = page.locator(".d-item", has_text="Journal of Biomedical Informatics").first
            assert "DHET eligible" in await jbi.inner_text()
            assert "SANLiC covered (Hybrid)" in await jbi.inner_text()
            ieee = page.locator(".d-item", has_text="IEEE Journal of Biomedical and Health Informatics")
            assert "Not on DHET list" in await ieee.inner_text()
            brain = page.locator(".d-item", has_text="Brain Informatics")
            assert "OA · USD 1,140" in await brain.inner_text() and "13 weeks to publication" in await brain.inner_text()
            disc = page.locator(".d-item", has_text="Informatics: X")
            assert "DHET: Discontinued" in await disc.inner_text()           # found through the CREST list
            if SHOTS:
                await page.screenshot(path=f"{SHOTS}/phone_find_title.png", full_page=True)
            await page.check("label.check:has-text('DHET eligible') input")
            assert await page.locator(".d-item").count() == 1
            assert await page.evaluate("document.documentElement.scrollWidth") <= 393

            # Open a result -> instant report; Back returns to the results
            await page.click(".d-item")
            await page.wait_for_selector("h2:has-text('Journal of Biomedical Informatics')")
            await page.click("text=‹ Back")
            await page.wait_for_selector(".d-item")

            # Scope mode: ranked by matching papers; Scopus unavailable is reported, not fatal
            await page.click("text=‹ Back")
            await page.fill("input[name=fq]", "clinical decision support")
            await page.select_option("select[name=fmode]", "scope")
            await page.click("button:has-text('Find journals')")
            await page.wait_for_selector(".d-item")
            first = await page.locator(".d-item").first.inner_text()
            assert "Journal of Biomedical Informatics" in first and "320 matching papers (OpenAlex)" in first
            assert await page.is_visible("text=Not available this time: Scopus")
            if SHOTS:
                await page.screenshot(path=f"{SHOTS}/phone_find_scope.png", full_page=True)
            modes = [c["mode"] for c in fake.discover_calls]
            assert "title" in modes and "scope" in modes

            assert not [e for e in errors if "502" not in e], errors
            await browser.close()

    try:
        asyncio.run(run())
    finally:
        api.shutdown()
        web.shutdown()


def test_phone_saved_journals(tmp_path):
    from tests.reflist_samples import get_bytes_for

    fake = FakeSupabase()
    api, api_url = fake.serve()
    web, web_url = serve_web(api_url)
    pc = SyncEngine(Database(tmp_path / "pc.db"), {}, lambda s: None, lambda *a: {}, opener=fake.opener,
                    get_bytes=get_bytes_for(tmp_path), tmp_dir=tmp_path / "lists")
    pc.sign_in(api_url, "sb_publishable_test", EMAIL, PASSWORD)
    pc.cycle()

    async def sign_in(page):
        await page.fill("input[type=email]", EMAIL)
        await page.fill("input[type=password]", PASSWORD)
        await page.click("button:has-text('Sign in')")
        await page.wait_for_selector("text=Reports from your PC appear here.")

    async def run():
        async with pw.async_playwright() as p:
            browser = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            ctx = await browser.new_context(viewport={"width": 393, "height": 852}, is_mobile=True, has_touch=True)
            page = await ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
            await page.goto(web_url)
            await sign_in(page)
            assert await page.is_visible("button.saved-btn:has-text('Saved journals')")

            # Instant check, including the APC from OpenAlex, then star it
            await page.fill("input[name=q]", "1532-0480")
            await page.click("button:has-text('Check journal')")
            await page.wait_for_selector("h2:has-text('Journal of Biomedical Informatics')")
            assert await page.is_visible(".tile:has-text('APC') >> text=USD 3,550 (list price) · SANLiC covered")
            await page.click("button.star")
            await page.wait_for_selector("button.star.on:has-text('Saved')")
            row = fake.tables["journal_saved"]["1532-0464"]
            assert row["title"] == "Journal of Biomedical Informatics" and row["report"]["quick"] is True

            # Saved table
            await page.click("text=‹ Back")
            await page.click("button.saved-btn:has-text('(1)')")
            await page.wait_for_selector(".saved-table")
            cells = await page.locator(".saved-row td").all_inner_texts()
            assert "Journal of Biomedical Informatics" in cells[0] and "10.8" in cells[1] and "Q1" in cells[1]
            assert cells[2] == "Eligible" and cells[3] == "Covered" and cells[5] == "USD 3,550 (list price)"
            if SHOTS:
                await page.screenshot(path=f"{SHOTS}/phone_saved.png", full_page=True)

            # Open it: full saved report with Check again; Back returns to the table
            await page.click(".saved-row")
            await page.wait_for_selector("button:has-text('Check again now')")
            assert await page.is_visible("button.star.on")
            await page.click("text=‹ Back")
            await page.wait_for_selector(".saved-table")

            # Survives signing out and in again (it lives in Supabase, not just on the phone)
            await page.click("text=‹ Back")
            await page.click("button:has-text('Sign out')")
            await sign_in(page)
            await page.wait_for_selector("button.saved-btn:has-text('(1)')")

            # The PC sees it too
            await asyncio.to_thread(pc.cycle)
            assert [s["uid"] for s in pc.db.list_saved()] == ["1532-0464"]

            # Remove from the table
            await page.click("button.saved-btn")
            await page.click("button.unsave")
            await page.wait_for_selector("text=None yet")
            assert fake.tables["journal_saved"]["1532-0464"]["deleted"] is True

            assert not errors, errors
            await browser.close()

    try:
        asyncio.run(run())
    finally:
        api.shutdown()
        web.shutdown()


def test_phone_find_star_and_filters(tmp_path):
    from tests.reflist_samples import get_bytes_for

    fake = FakeSupabase()
    api, api_url = fake.serve()
    web, web_url = serve_web(api_url)
    pc = SyncEngine(Database(tmp_path / "pc.db"), {}, lambda s: None, lambda *a: {}, opener=fake.opener,
                    get_bytes=get_bytes_for(tmp_path), tmp_dir=tmp_path / "lists")
    pc.sign_in(api_url, "sb_publishable_test", EMAIL, PASSWORD)
    pc.cycle()

    async def run():
        async with pw.async_playwright() as p:
            browser = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            ctx = await browser.new_context(viewport={"width": 393, "height": 852}, is_mobile=True, has_touch=True)
            page = await ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(web_url)
            await page.fill("input[type=email]", EMAIL)
            await page.fill("input[type=password]", PASSWORD)
            await page.click("button:has-text('Sign in')")
            await page.wait_for_selector("text=Reports from your PC appear here.")
            await page.fill("input[name=fq]", "informatics")
            await page.click("button:has-text('Find journals')")
            await page.wait_for_selector(".d-item")
            total = await page.locator(".d-row").count()

            # Star straight from the list
            await page.click(".d-row:has-text('Journal of Biomedical Informatics') >> nth=0 >> .d-star")
            await page.wait_for_selector(".d-row:has-text('Journal of Biomedical Informatics') .d-star.on")
            row = fake.tables["journal_saved"]["1532-0464"]
            assert row["report"]["from_search"] is True
            assert row["report"]["sources"]["crest"]["data"]["eligible"] is True

            # More filters
            await page.click("summary:has-text('More filters')")
            await page.fill("label.d-field:has-text('Max APC') input", "2000")
            await page.locator("label.d-field:has-text('Max APC') input").press("Enter")
            await page.check("label.check:has-text('Hide unknown APC') input")
            titles = await page.locator(".d-item b").all_inner_texts()
            assert set(titles) == {"Brain Informatics", "Health Informatics Journal"}, titles
            assert await page.is_visible("summary:has-text('(2 on)')") or await page.is_visible("summary:has-text('on)')")
            await page.click("button:has-text('Clear filters')")
            assert await page.locator(".d-row").count() == total
            await page.fill("label.d-field:has-text('Publisher') input", "elsevier")
            await page.locator("label.d-field:has-text('Publisher') input").press("Enter")
            assert set(await page.locator(".d-item b").all_inner_texts()) == {"Journal of Biomedical Informatics", "Informatics in Medicine Unlocked"}
            await page.click("button:has-text('Clear filters')")
            await page.check("label.check:has-text('Saved journals only') input")
            assert await page.locator(".d-item b").all_inner_texts() == ["Journal of Biomedical Informatics"]
            await page.click("button:has-text('Clear filters')")
            await page.select_option(".d-controls select", "apc")
            first = await page.locator(".d-item b").first.inner_text()
            assert first == "Brain Informatics"
            if SHOTS:
                await page.click("summary:has-text('More filters')") if not await page.is_visible("label.d-field") else None
                await page.screenshot(path=f"{SHOTS}/phone_find_filters.png", full_page=True)

            # The saved search result opens as a report with Check again
            await page.click("text=‹ Back")
            await page.click("button.saved-btn:has-text('(1)')")
            await page.click(".saved-row")
            await page.wait_for_selector("button:has-text('Check again now')")
            assert await page.is_visible(".tile:has-text('DHET subsidy')")

            assert not errors, errors
            await browser.close()

    try:
        asyncio.run(run())
    finally:
        api.shutdown()
        web.shutdown()
