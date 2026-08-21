import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import jsonschema
import pytest

from scrape_gateway.models import ScrapeRequest
from scrape_gateway.providers.scrapingant import ScrapingAntProvider

ROOT = Path(__file__).parents[1]
CONTRACTS = ROOT / "src" / "scrape_gateway" / "provider_contracts" / "v1"
CONTRACT_PATHS = tuple(
    path
    for path in sorted(CONTRACTS.glob("*.json"))
    if path.name not in {"schema.json", "template.json"}
)


def _entrypoint_names() -> frozenset[str]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    return frozenset(project["project"]["entry-points"]["scrape_gateway.providers"])


@pytest.mark.parametrize("contract_path", CONTRACT_PATHS, ids=lambda path: path.stem)
def test_every_provider_contract_is_valid_and_registered(contract_path: Path) -> None:
    schema = json.loads((CONTRACTS / "schema.json").read_text(encoding="utf-8"))
    contract = json.loads(contract_path.read_text(encoding="utf-8"))

    jsonschema.validate(contract, schema, format_checker=jsonschema.FormatChecker())
    assert contract["provider"] == contract_path.stem
    assert contract["provider"] in _entrypoint_names()


def test_contract_validator_discovers_the_same_contract_set() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/validate_provider_contracts.py", "--json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(result.stdout)
    assert summary == {
        "schema_version": 1,
        "ok": True,
        "count": len(CONTRACT_PATHS),
        "providers": [path.stem for path in CONTRACT_PATHS],
    }


def test_scrapingant_contract_matches_the_adapter() -> None:
    contract = json.loads((CONTRACTS / "scrapingant.json").read_text(encoding="utf-8"))
    assert contract["endpoint"] == {
        "method": "GET",
        "url": "https://api.scrapingant.com/v2/general",
    }
    assert contract["auth"]["environment"] == ["SCRAPINGANT_API_KEY"]
    assert contract["response"]["target_status"]["name"] == "Ant-page-status-code"
    assert contract["response"]["target_headers"]["prefix"] == "Ant-Original-Header-"
    assert contract["response"]["cost"]["name"] == "Ant-credits-cost"

    provider = ScrapingAntProvider(api_key="test")
    shapes = {
        "http_datacenter": ScrapeRequest("https://example.com"),
        "browser_datacenter": ScrapeRequest("https://example.com", render_js=True),
        "http_residential": ScrapeRequest("https://example.com", premium=True),
        "browser_residential": ScrapeRequest("https://example.com", render_js=True, premium=True),
    }
    assert {route: provider.estimated_cost_units(request) for route, request in shapes.items()} == {
        route: spec["estimated_cost"]["units"] for route, spec in contract["routes"].items()
    }


def test_documented_provider_counts_match_registered_entrypoints() -> None:
    count = len(_entrypoint_names())
    assert count == 16
    documented = {
        ROOT / "README.md": (f"{count} built-in providers",),
        ROOT / "docs" / "providers.md": (f"{count} built-in",),
        ROOT / "docs" / "SKILL.md": (f"{count} built-in",),
    }
    for path, expected_phrases in documented.items():
        text = path.read_text(encoding="utf-8")
        for phrase in expected_phrases:
            assert phrase in text, f"{path.relative_to(ROOT)} is missing {phrase!r}"


def test_provider_guide_requires_contract_and_sev_handoffs() -> None:
    guide = (ROOT / "docs" / "references" / "adding-built-in-provider.md").read_text(
        encoding="utf-8"
    )
    assert "provider_contracts/v1/<name>.json" in guide
    assert "sev-engine-integration.md" in guide


def test_validator_module_is_importable_without_running_cli() -> None:
    spec = importlib.util.spec_from_file_location(
        "validate_provider_contracts", ROOT / "scripts" / "validate_provider_contracts.py"
    )
    assert spec is not None and spec.loader is not None
