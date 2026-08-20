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
from scrape_gateway.models import FailureReason, ScrapeRequest
from scrape_gateway.providers.scrapedrive import ScrapeDriveProvider

_load_dotenv()
API_KEY = os.getenv("SCRAPEDRIVE_API_KEY")

pytestmark = pytest.mark.skipif(not API_KEY, reason="SCRAPEDRIVE_API_KEY not set")


@pytest.fixture
def provider():
    return ScrapeDriveProvider(api_key=API_KEY)


@pytest.fixture
def provider_auto():
    return ScrapeDriveProvider(api_key=API_KEY, auto=True)


class TestStandardTier:
    async def test_simple_page(self, provider):
        result = await provider.scrape(ScrapeRequest(url="https://example.com"))
        assert result.success is True
        assert result.status_code == 200
        assert result.route == "scrapedrive:standard"
        assert result.cost_units == 5
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
        assert result.cost_units == 10

    async def test_country_starts_at_advanced(self, provider):
        result = await provider.scrape(ScrapeRequest(url="https://example.com", country="US"))
        assert result.success is True
        assert result.route == "scrapedrive:advanced"
        assert result.cost_units == 10

    async def test_premium_starts_at_hyperdrive(self, provider):
        result = await provider.scrape(ScrapeRequest(url="https://example.com", premium=True))
        assert result.success is True
        assert result.route == "scrapedrive:hyperdrive"
        assert result.cost_units == 15


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
        assert result.screenshot is not None
        assert len(result.screenshot) > 100


class TestSpecFields:
    async def test_markdown_output(self, provider):
        result = await provider.scrape(
            ScrapeRequest(url="https://www.iana.org/help/example-domains", output_format="markdown")
        )
        assert result.success is True
        assert result.markdown is not None
        # page_markdown extracts the main content and keeps markdown syntax; the plain
        # HTML fetch would carry tags instead.
        assert "# Example Domains" in result.markdown
        assert "<html" not in result.markdown.lower()

    async def test_job_id_is_returned(self, provider):
        result = await provider.scrape(ScrapeRequest(url="https://example.com"))
        assert result.success is True
        assert result.metadata["job_id"]

    async def test_timeout_below_the_spec_minimum_is_still_accepted(self, provider):
        # A raw timeout_ms of 5000 is a 422; the adapter clamps it to the 10s minimum.
        result = await provider.scrape(ScrapeRequest(url="https://example.com", timeout_seconds=5))
        assert result.status_code == 200


class TestAutoMode:
    async def test_auto_job_runs_once(self, provider_auto):
        result = await provider_auto.scrape(ScrapeRequest(url="https://example.com"))
        assert result.success is True
        assert result.route == "scrapedrive:auto"
        assert len(result.attempt_ledger) == 1
        assert "Example Domain" in (result.html or "")

    async def test_auto_honours_a_tight_budget(self, provider_auto):
        result = await provider_auto.scrape(
            ScrapeRequest(url="https://example.com", metadata={"_remaining_cost_units": 5})
        )
        assert result.success is True
        assert result.metadata["max_credits"] == 5

    async def test_a_country_request_falls_back_to_the_ladder(self, provider_auto):
        # Auto rejects proxy_country outright, so this must not be sent as an Auto job.
        result = await provider_auto.scrape(ScrapeRequest(url="https://example.com", country="US"))
        assert result.success is True
        assert result.route == "scrapedrive:advanced"


class TestAsyncMode:
    """A timeout past the sync ceiling has to go to the async host."""

    async def test_a_long_timeout_runs_as_a_job(self, provider):
        result = await provider.scrape(
            ScrapeRequest(url="https://example.com", timeout_seconds=200)
        )
        assert result.success is True
        assert result.metadata["mode"] == "async"
        assert "Example Domain" in (result.html or "")
        # The finished job states its own price, unlike every sync response.
        assert result.metadata["cost_provenance"] == "exact"
        assert result.cost_units == 5

    async def test_an_unreachable_target_is_reported_as_a_failure(self, provider):
        # The job still reports status "completed"; only the inner status_code of
        # 0 and the reason say it never reached anything.
        result = await provider.scrape(
            ScrapeRequest(
                url="https://this-domain-does-not-exist-xyz123.invalid", timeout_seconds=200
            )
        )
        assert result.success is False
        assert result.cost_units == 0
        assert result.error

    async def test_an_auto_job_survives_the_json_body(self, provider_auto):
        # Sent as a query string this is a 500 saying max_credits must be positive.
        result = await provider_auto.scrape(
            ScrapeRequest(url="https://example.com", timeout_seconds=200)
        )
        assert result.success is True
        assert result.route == "scrapedrive:auto"


class TestHeaderForwarding:
    """Verified 2026-08-20: async forwards sdrive- headers, sync does not."""

    async def test_async_forwards_a_caller_header_to_the_target(self, provider):
        result = await provider.scrape(
            ScrapeRequest(
                url="https://httpbin.org/headers",
                headers={"X-Sgw-Probe": "forwarded-ok"},
                timeout_seconds=200,
            )
        )
        assert result.success is True
        assert "forwarded-ok" in (result.html or "")

    async def test_sync_warns_that_it_will_drop_them(self, provider, capsys):
        # example.com rather than httpbin: this asserts the warning, not the
        # scrape, and httpbin fails often enough to escalate all three tiers.
        # That the sync host really ignores the header is covered by the unit
        # test and was confirmed by hand against httpbin.
        await provider.scrape(
            ScrapeRequest(url="https://example.com", headers={"X-Sgw-Probe": "dropped"})
        )
        assert "does not forward sdrive- headers" in capsys.readouterr().err


class TestBlockedTargets:
    async def test_a_block_is_reported_as_a_block_not_a_server_error(self, provider):
        # Without transparent_mode this is ScrapeDrive's own HTTP 500 JSON error,
        # which the classifier reads as http_5xx — the provider blamed for the
        # target's anti-bot. g2.com is behind DataDome and refuses a plain fetch.
        result = await provider.scrape(
            ScrapeRequest(
                url="https://www.g2.com/products/notion/reviews",
                metadata={"start_tier": "scrapedrive:standard"},
                timeout_seconds=60,
            )
        )
        assert result.success is False
        # The first attempt is the one that reached g2 and was refused. Later tiers
        # can still end as a genuine ScrapeDrive 500: the spec keeps its JSON error
        # for failures with no target response at all, such as an internal timeout,
        # and transparent_mode has nothing to substitute in those.
        first = result.attempt_ledger[0]
        assert first.status_code == 403
        assert first.failure_reason is FailureReason.HTTP_403


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
