import os
import tempfile

# Keep test downloads and caches out of the real cache folder.
os.environ.setdefault("JOURNAL_SCOPE_CACHE", tempfile.mkdtemp(prefix="journal-scope-test-cache-"))
