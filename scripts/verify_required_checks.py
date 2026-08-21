#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REQUIRED_CHECKS = (
    "quality (3.11)",
    "quality (3.12)",
    "quality (3.13)",
    "package",
    "container",
)


def evaluate(payload: dict[str, Any]) -> dict[str, str]:
    conclusions = {
        run.get("name"): run.get("conclusion")
        for run in payload.get("check_runs", [])
        if run.get("status") == "completed"
    }
    failed = {
        name: conclusions.get(name, "missing")
        for name in REQUIRED_CHECKS
        if conclusions.get(name) != "success"
    }
    if failed:
        details = ", ".join(f"{name}={status}" for name, status in failed.items())
        raise ValueError(f"required checks are not green: {details}")
    return {name: conclusions[name] for name in REQUIRED_CHECKS}


def github_payload(repository: str, revision: str) -> dict[str, Any]:
    result = subprocess.run(
        [
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            f"repos/{repository}/commits/{revision}/check-runs?per_page=100",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description="Require the stable SGW CI checks to be green.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="Read a check-runs API fixture.")
    source.add_argument("--repository", help="GitHub owner/repository.")
    parser.add_argument("--revision", help="Full commit SHA when querying GitHub.")
    args = parser.parse_args()
    if args.repository and not args.revision:
        parser.error("--revision is required with --repository")
    try:
        payload = (
            json.loads(args.input.read_text(encoding="utf-8"))
            if args.input
            else github_payload(args.repository, args.revision)
        )
        checks = evaluate(payload)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"required-check validation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "checks": checks}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
