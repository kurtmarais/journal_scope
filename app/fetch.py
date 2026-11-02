"""HTTP fetching with an optional headless-browser fallback.

Plain HTTP (httpx) is used where a site serves its data directly. Sites that
render in the browser (Kanalregister) or sit behind bot protection
(SCImago's Cloudflare, most publisher sites) are loaded in headless Chromium
via Playwright, the same way a person's browser would load them.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)

CACHE_DIR = Path(os.environ.get("JOURNAL_SCOPE_CACHE", Path.home() / ".cache" / "journal_scope"))

# Markers of an anti-bot interstitial rather than real content.
_CHALLENGE_MARKERS = (
    "cf-chl-",
    "challenge-platform/h/",
    "Just a moment...",
    "Attention Required! | Cloudflare",
    "Please enable JavaScript and cookies to continue",
    "Checking your browser before accessing",
)


class FetchError(Exception):
    pass


def looks_blocked(status: int, body: str) -> bool:
    if status in (401, 403, 429, 503):
        return True
    head = body[:20000]
    return any(m in head for m in _CHALLENGE_MARKERS)


class Fetcher:
    def __init__(
        self,
        use_browser: bool = True,
        chromium_path: str | None = None,
        timeout: float = 30.0,
        ttl: float = 6 * 3600,
    ) -> None:
        self.use_browser = use_browser and os.environ.get("JOURNAL_SCOPE_NO_BROWSER") != "1"
        self.chromium_path = chromium_path or os.environ.get("JOURNAL_SCOPE_CHROMIUM") or None
        self.timeout = timeout
        self.ttl = ttl
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-GB,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            follow_redirects=True,
            timeout=timeout,
        )
        self._cache: dict[str, tuple[float, str]] = {}
        self._pw = None
        self._browser = None
        self._browser_lock = asyncio.Lock()
        self._browser_slots = asyncio.Semaphore(3)

    # ------------------------------------------------------------------ http
    async def get(self, url: str, headers: dict | None = None) -> httpx.Response:
        try:
            return await self._client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise FetchError(f"{type(exc).__name__}: {exc}") from exc

    async def get_text(self, url: str, headers: dict | None = None) -> str:
        key = "get:" + url
        hit = self._cache_get(key)
        if hit is not None:
            return hit
        resp = await self.get(url, headers=headers)
        if resp.status_code >= 400:
            raise FetchError(f"HTTP {resp.status_code} from {url}")
        self._cache[key] = (time.time(), resp.text)
        return resp.text

    async def get_file(self, url: str, max_age: float = 7 * 86400) -> Path:
        """Download a file to the on-disk cache (re-used for `max_age` seconds)."""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha1(url.encode()).hexdigest()[:16] + "_" + url.rsplit("/", 1)[-1][:80]
        path = CACHE_DIR / name
        if path.exists() and time.time() - path.stat().st_mtime < max_age:
            return path
        resp = await self.get(url)
        if resp.status_code >= 400:
            if path.exists():  # stale copy is better than nothing
                log.warning("Using stale cached copy of %s (HTTP %s)", url, resp.status_code)
                return path
            raise FetchError(f"HTTP {resp.status_code} from {url}")
        path.write_bytes(resp.content)
        return path

    # --------------------------------------------------------------- browser
    async def _ensure_browser(self):
        async with self._browser_lock:
            if self._browser is not None:
                return self._browser
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:  # pragma: no cover - depends on install
                raise FetchError("Playwright is not installed (pip install playwright)") from exc
            self._pw = await async_playwright().start()
            kwargs = {"headless": True, "args": ["--no-sandbox"]}
            if self.chromium_path:
                kwargs["executable_path"] = self.chromium_path
            try:
                self._browser = await self._pw.chromium.launch(**kwargs)
            except Exception as exc:
                raise FetchError(
                    "Could not start Chromium. Run `playwright install chromium` "
                    "or set JOURNAL_SCOPE_CHROMIUM to a Chromium executable. "
                    f"({exc})"
                ) from exc
            return self._browser

    async def render(
        self,
        url: str,
        wait_for: str | None = None,
        wait_ms: int = 15000,
        settle_ms: int = 800,
    ) -> str:
        """Load `url` in headless Chromium and return the rendered HTML."""
        if not self.use_browser:
            raise FetchError("Browser rendering is disabled")
        key = f"render:{url}|{wait_for}"
        hit = self._cache_get(key)
        if hit is not None:
            return hit
        browser = await self._ensure_browser()
        async with self._browser_slots:
            context = await browser.new_context(user_agent=USER_AGENT, locale="en-GB")
            try:
                page = await context.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
                except Exception as exc:
                    raise FetchError(f"Could not load {url}: {exc}") from exc
                if wait_for:
                    try:
                        await page.wait_for_selector(wait_for, timeout=wait_ms)
                    except Exception:
                        log.info("Timed out waiting for %r on %s", wait_for, url)
                else:
                    try:
                        await page.wait_for_load_state("networkidle", timeout=wait_ms)
                    except Exception:
                        pass
                await page.wait_for_timeout(settle_ms)
                html = await page.content()
            finally:
                await context.close()
        self._cache[key] = (time.time(), html)
        return html

    async def page_html(self, url: str, wait_for: str | None = None) -> tuple[str, str]:
        """Fetch a page over HTTP, falling back to the browser if blocked.

        Returns (html, method) where method is "http" or "browser".
        """
        try:
            resp = await self.get(url)
            body = resp.text
            if not looks_blocked(resp.status_code, body) and resp.status_code < 400:
                return body, "http"
            reason = f"HTTP {resp.status_code}"
        except FetchError as exc:
            reason = str(exc)
        if not self.use_browser:
            raise FetchError(f"{url} refused a plain request ({reason}) and browser rendering is disabled")
        html = await self.render(url, wait_for=wait_for)
        if looks_blocked(200, html):
            raise FetchError(f"{url} served a bot check that the headless browser could not pass")
        return html, "browser"

    # ----------------------------------------------------------------- misc
    def _cache_get(self, key: str) -> str | None:
        item = self._cache.get(key)
        if item and time.time() - item[0] < self.ttl:
            return item[1]
        return None

    async def close(self) -> None:
        await self._client.aclose()
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()
        self._browser = None
        self._pw = None
