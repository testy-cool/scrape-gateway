"""Live integration tests against the ScraperAPI service.

Run with: pytest tests/test_scraperapi_live.py -v
Requires SCRAPERAPI_API_KEY in env or .env.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scrape_gateway.config import _load_dotenv
from scrape_gateway.models import ScrapeRequest
from scrape_gateway.providers.scraperapi import ScraperApiProvider

_load_dotenv()
API_KEY = os.getenv("SCRAPERAPI_API_KEY")

pytestmark = pytest.mark.skipif(not API_KEY, reason="SCRAPERAPI_API_KEY not set")


@pytest.fixture
def provider():
    return ScraperApiProvider(api_key=API_KEY)


class TestBasic:
    async def test_simple_page(self, provider):
        result = await provider.scrape(ScrapeRequest(url="https://example.com"))
        assert result.success is True
        assert result.status_code == 200
        assert result.route == "scraperapi"
        assert result.cost_units == 1
        assert "Example Domain" in (result.html or "")

    async def test_returns_html(self, provider):
        result = await provider.scrape(ScrapeRequest(url="https://httpbin.org/html"))
        assert result.success is True
        assert "<html" in (result.html or "").lower()
        assert len(result.html or "") > 100


class TestParams:
    async def test_country(self, provider):
        result = await provider.scrape(ScrapeRequest(url="https://example.com", country="US"))
        assert result.success is True
        assert "Example Domain" in (result.html or "")

    async def test_render_js(self, provider):
        result = await provider.scrape(ScrapeRequest(url="https://example.com", render_js=True))
        assert result.success is True
        assert result.cost_units == 10
        assert len(result.html or "") > 100

    async def test_premium(self, provider):
        result = await provider.scrape(ScrapeRequest(url="https://example.com", premium=True))
        assert result.success is True
        assert result.route == "scraperapi:premium"
        assert result.cost_units == 10

    async def test_premium_render(self, provider):
        result = await provider.scrape(
            ScrapeRequest(url="https://example.com", premium=True, render_js=True)
        )
        assert result.success is True
        assert result.route == "scraperapi:premium"
        assert result.cost_units == 25


class TestErrorHandling:
    async def test_invalid_url(self, provider):
        result = await provider.scrape(
            ScrapeRequest(url="https://this-domain-does-not-exist-xyz123.com")
        )
        assert result.success is False

    async def test_bad_api_key(self):
        provider = ScraperApiProvider(api_key="invalid_key_000")
        result = await provider.scrape(ScrapeRequest(url="https://example.com"))
        assert result.success is False
