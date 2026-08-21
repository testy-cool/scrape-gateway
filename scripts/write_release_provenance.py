#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Write machine-readable SGW release provenance.")
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow-run", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image-digest")
    args = parser.parse_args()
    with (ROOT / "pyproject.toml").open("rb") as handle:
        version = tomllib.load(handle)["project"]["version"]
    artifacts = [
        {"name": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in sorted(args.dist.iterdir())
        if path.is_file() and path.name not in {args.output.name, "SHA256SUMS"}
    ]
    payload = {
        "schema_version": 1,
        "repository": args.repository,
        "tag": args.tag,
        "version": version,
        "revision": args.revision,
        "workflow_run": args.workflow_run,
        "artifacts": artifacts,
    }
    if args.image_digest:
        payload["image"] = {
            "reference": f"ghcr.io/{args.repository}@{args.image_digest}",
            "digest": args.image_digest,
        }
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"provenance=ok output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
