#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE = "ghcr.io/testy-cool/scrape-gateway"


def image_reference(digest: str) -> str:
    if not DIGEST.fullmatch(digest):
        raise ValueError("digest must match sha256:<64 lowercase hex characters>")
    return f"{IMAGE}@{digest}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an immutable SGW production digest.")
    parser.add_argument("digest")
    args = parser.parse_args()
    try:
        reference = image_reference(args.digest)
    except ValueError as exc:
        print(f"image digest validation failed: {exc}", file=sys.stderr)
        return 1
    print(reference)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
