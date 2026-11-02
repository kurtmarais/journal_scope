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
