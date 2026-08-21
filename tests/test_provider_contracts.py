import json
from pathlib import Path

import jsonschema

from scrape_gateway.models import ScrapeRequest
from scrape_gateway.providers.scrapingant import ScrapingAntProvider

CONTRACTS = Path(__file__).parents[1] / "src" / "scrape_gateway" / "provider_contracts" / "v1"


def test_scrapingant_contract_is_valid_and_matches_the_adapter() -> None:
    schema = json.loads((CONTRACTS / "schema.json").read_text(encoding="utf-8"))
    contract = json.loads((CONTRACTS / "scrapingant.json").read_text(encoding="utf-8"))

    jsonschema.validate(contract, schema)
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


def test_provider_guide_requires_contract_updates() -> None:
    guide = (
        Path(__file__).parents[1] / "docs" / "references" / "adding-built-in-provider.md"
    ).read_text(encoding="utf-8")
    assert "provider_contracts/v1/<name>.json" in guide
    assert "SEV vendors a pinned snapshot" in guide
