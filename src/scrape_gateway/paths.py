from __future__ import annotations

import re
from pathlib import Path

CACHE_KEY_PATTERN = re.compile(r"^[0-9a-f]{24}$")
RUN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")


def safe_child(root: str | Path, name: str, *, pattern: re.Pattern[str]) -> Path:
    """Return a validated, resolved child path contained by root."""

    if not isinstance(name, str) or not pattern.fullmatch(name):
        raise ValueError(f"Invalid child name: {name!r}")

    try:
        resolved_root = Path(root).resolve()
        resolved = (resolved_root / name).resolve()
        resolved.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Child path escapes root: {name!r}") from exc
    return resolved
