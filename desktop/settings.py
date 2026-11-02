"""Settings stored as JSON in the data folder."""

from __future__ import annotations

import json
from pathlib import Path

DEFAULTS = {"auto_update": True}


def load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {**DEFAULTS, **(data if isinstance(data, dict) else {})}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save(path: Path, settings: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    tmp.replace(path)
