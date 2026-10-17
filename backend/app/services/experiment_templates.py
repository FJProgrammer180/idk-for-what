"""Bundled experiment templates (read from ``experiments/templates/*.json``).

Templates are data, not code: each one names a registered method/simulation and a
parameter dict, so instantiating one goes through exactly the same validation as
a hand-built request.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import REPO_ROOT

TEMPLATE_DIR = Path(REPO_ROOT) / "experiments" / "templates"


@lru_cache(maxsize=1)
def _load() -> dict[str, dict[str, Any]]:
    templates: dict[str, dict[str, Any]] = {}
    if not TEMPLATE_DIR.exists():
        return templates
    for path in sorted(TEMPLATE_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        key = str(data.get("key") or path.stem)
        data["key"] = key
        data["_path"] = str(path.relative_to(REPO_ROOT))
        templates[key] = data
    return templates


def list_templates() -> list[dict[str, Any]]:
    return [
        {k: v for k, v in template.items() if not k.startswith("_")}
        for template in _load().values()
    ]


def template_by_key(key: str) -> dict[str, Any] | None:
    template = _load().get(str(key))
    if template is None:
        return None
    return {k: v for k, v in template.items() if not k.startswith("_")}


def clear_cache() -> None:
    _load.cache_clear()
