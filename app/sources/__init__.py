"""One module per website the report draws on.

Each module exposes pure `parse_*` functions (unit-tested against saved pages)
and an async `lookup(...)` that fetches and returns a `SourceResult` dict.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

FOUND = "found"
NOT_FOUND = "not_found"
ERROR = "error"
SKIPPED = "skipped"


def source_result(
    source: str,
    status: str,
    url: str | None = None,
    data: dict[str, Any] | None = None,
    error: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "status": status,
        "url": url,
        "data": data or {},
        "error": error,
        "note": note,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
