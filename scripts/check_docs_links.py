#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^]]*\]\(([^)]+)\)")


def tracked_markdown() -> tuple[Path, ...]:
    output = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.md"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return tuple(
        ROOT / item.decode()
        for item in output.split(b"\0")
        if item and not item.decode().startswith("docs/archive/")
    )


def local_target(raw: str) -> str | None:
    target = raw.strip()
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")]
    else:
        target = target.split(maxsplit=1)[0]
    if target.startswith(("http://", "https://", "mailto:", "#")):
        return None
    return unquote(target.split("#", 1)[0].split("?", 1)[0])


def main() -> int:
    checked = 0
    failures: list[str] = []
    files = tracked_markdown()
    for path in files:
        text = path.read_text(encoding="utf-8")
        for match in LINK.finditer(text):
            target = local_target(match.group(1))
            if not target:
                continue
            checked += 1
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                failures.append(f"{path.relative_to(ROOT)}: missing {match.group(1)!r}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"docs_links=ok files={len(files)} local_links={checked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
