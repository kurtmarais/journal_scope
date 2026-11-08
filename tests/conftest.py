import os
import tempfile

# Keep test downloads and caches out of the real cache folder.
os.environ.setdefault("JOURNAL_SCOPE_CACHE", tempfile.mkdtemp(prefix="journal-scope-test-cache-"))

import sys

import pytest


@pytest.fixture(autouse=True)
def _reset_web_hooks():
    """The desktop test installs Qt hooks on the shared FastAPI app; later tests must not use them."""
    yield
    web = sys.modules.get("app.main")
    if web is not None:
        web.app.state.renderer = None
        web.app.state.download = None
