"""Live integration tests against the ScrapeDrive API.

Run with: pytest tests/test_scrapedrive_live.py -v
Requires SCRAPEDRIVE_API_KEY in env or .env.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scrape_gateway.config import _load_dotenv
from scrape_gateway.models import ScrapeRequest
from scrape_gateway.providers.scrapedrive import ScrapeDriveProvider

_load_dotenv()
API_KEY = os.getenv("SCRAPEDRIVE_API_KEY")

pytestmark = pytest.mark.skipif(not API_KEY, reason="SCRAPEDRIVE_API_KEY not set")


@pytest.fixture
def provider():
    return ScrapeDriveProvider(api_key=API_KEY)


class TestStandardTier:
    async def test_simple_page(self, provider):
        result = await provider.scrape(ScrapeRequest(url="https://example.com"))
        assert result.success is True
        assert result.status_code == 200
        assert result.route == "scrapedrive:standard"
        assert result.cost_units == 1
        assert "Example Domain" in (result.html or "")

    async def test_returns_html(self, provider):
        result = await provider.scrape(ScrapeRequest(url="https://httpbin.org/html"))
        assert result.success is True
        assert "<html" in (result.html or "").lower()
        assert len(result.html or "") > 100


class TestTierEscalation:
    async def test_start_tier_advanced(self, provider):
        result = await provider.scrape(
            ScrapeRequest(
                url="https://example.com",
                metadata={"start_tier": "scrapedrive:advanced"},
            )
        )
        assert result.success is True
        assert result.route == "scrapedrive:advanced"
        assert result.cost_units == 5

    async def test_country_starts_at_advanced(self, provider):
        result = await provider.scrape(ScrapeRequest(url="https://example.com", country="US"))
        assert result.success is True
        assert result.route == "scrapedrive:advanced"
        assert result.cost_units == 5

    async def test_premium_starts_at_hyperdrive(self, provider):
        result = await provider.scrape(ScrapeRequest(url="https://example.com", premium=True))
        assert result.success is True
        assert result.route == "scrapedrive:hyperdrive"
        assert result.cost_units == 25


class TestParams:
    async def test_mobile_device(self, provider):
        result = await provider.scrape(ScrapeRequest(url="https://example.com", mobile=True))
        assert result.success is True

    async def test_html_always_returned(self, provider):
        result = await provider.scrape(ScrapeRequest(url="https://example.com"))
        assert result.success is True
        assert result.html is not None
        assert "<html" in result.html.lower()

    async def test_render_js(self, provider):
        result = await provider.scrape(ScrapeRequest(url="https://example.com", render_js=True))
        assert result.success is True
        assert len(result.html or "") > 100

    async def test_screenshot(self, provider):
        result = await provider.scrape(ScrapeRequest(url="https://example.com", screenshot=True))
        assert result.success is True
        screenshot_url = result.metadata.get("screenshot_url")
        assert screenshot_url is not None or result.screenshot is not None


class TestErrorHandling:
    async def test_invalid_url(self, provider):
        result = await provider.scrape(
            ScrapeRequest(url="https://this-domain-does-not-exist-xyz123.com")
        )
        assert result.success is False

    async def test_bad_api_key(self):
        provider = ScrapeDriveProvider(api_key="invalid_key")
        result = await provider.scrape(ScrapeRequest(url="https://example.com"))
        assert result.success is False
