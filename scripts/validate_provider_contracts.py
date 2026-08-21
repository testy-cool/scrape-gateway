#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = ROOT / "src" / "scrape_gateway" / "provider_contracts" / "v1"
METADATA_FILES = frozenset({"schema.json", "template.json"})


def contract_paths() -> tuple[Path, ...]:
    return tuple(
        path for path in sorted(CONTRACT_ROOT.glob("*.json")) if path.name not in METADATA_FILES
    )


def registered_providers() -> frozenset[str]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    entries = project["project"]["entry-points"]["scrape_gateway.providers"]
    return frozenset(entries)


def validate_contracts() -> list[dict[str, Any]]:
    schema = json.loads((CONTRACT_ROOT / "schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    )
    registered = registered_providers()
    contracts: list[dict[str, Any]] = []
    providers: set[str] = set()

    paths = contract_paths()
    if not paths:
        raise ValueError("no provider contracts found")

    for path in paths:
        contract = json.loads(path.read_text(encoding="utf-8"))
        errors = sorted(validator.iter_errors(contract), key=lambda error: list(error.path))
        if errors:
            details = "; ".join(error.message for error in errors)
            raise ValueError(f"{path.relative_to(ROOT)}: {details}")
        provider = contract["provider"]
        if provider != path.stem:
            raise ValueError(f"{path.name}: provider must be {path.stem!r}, got {provider!r}")
        if provider in providers:
            raise ValueError(f"duplicate provider contract: {provider}")
        if provider not in registered:
            raise ValueError(f"{path.name}: no built-in entry point for {provider!r}")
        providers.add(provider)
        contracts.append(contract)
    return contracts


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate every built-in provider wire contract.")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable summary.")
    args = parser.parse_args()
    try:
        contracts = validate_contracts()
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as exc:
        print(f"provider contract validation failed: {exc}", file=sys.stderr)
        return 1

    summary = {
        "schema_version": 1,
        "ok": True,
        "count": len(contracts),
        "providers": [contract["provider"] for contract in contracts],
    }
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(
            f"validated {summary['count']} provider contract(s): {', '.join(summary['providers'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
