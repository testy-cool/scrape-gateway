#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG_PATTERN = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def project_version(root: Path = ROOT) -> str:
    with (root / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def validate_release(tag: str, revision: str, main_ref: str | None = None) -> dict[str, str]:
    match = TAG_PATTERN.fullmatch(tag)
    if not match:
        raise ValueError("tag must match vX.Y.Z with canonical non-negative integers")
    if not REVISION_PATTERN.fullmatch(revision):
        raise ValueError("revision must be a full lowercase 40-character commit SHA")

    version = project_version()
    if tag != f"v{version}":
        raise ValueError(f"tag {tag!r} does not match project version {version!r}")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if f"version-{version}-blue" not in readme:
        raise ValueError("README version badge does not match project version")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(
        rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.MULTILINE
    ):
        raise ValueError("CHANGELOG has no dated heading for project version")

    if main_ref:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", revision, main_ref],
            cwd=ROOT,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError(f"revision {revision} is not an ancestor of {main_ref}")

    return {"tag": tag, "version": version, "revision": revision}


def main() -> int:
    parser = argparse.ArgumentParser(description="Reject unsafe scrape-gateway releases.")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--main-ref")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = validate_release(args.tag, args.revision, args.main_ref)
    except ValueError as exc:
        print(f"release validation failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"ok": True, **result}, sort_keys=True))
    else:
        print(f"release_guard=ok tag={result['tag']} revision={result['revision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
