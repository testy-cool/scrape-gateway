"""Tests for provider adapters using respx to mock HTTP responses."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from scrape_gateway.models import FailureReason, ScrapeRequest, ScrapeResult
from scrape_gateway.providers.raw_http import RawHttpProvider
from scrape_gateway.providers.scrape_do import ScrapeDoProvider
from scrape_gateway.providers.scrapedrive import ScrapeDriveProvider
from scrape_gateway.providers.scraperapi import ScraperApiProvider
from scrape_gateway.providers.scrapingbee import ScrapingBeeProvider

GOOD_HTML = (
    "<html><head><title>Test</title></head><body>"
    "<p>This is a realistic page with enough content to pass all minimum length checks.</p>"
    "</body></html>"
)
TARGET_URL = "https://example.com/page"


@pytest.mark.parametrize(
    ("provider", "scrape_request", "expected"),
    [
        (RawHttpProvider(), ScrapeRequest(TARGET_URL), 0),
        (ScrapeDoProvider(token="token"), ScrapeRequest(TARGET_URL), 1),
        (
            ScrapeDoProvider(token="token"),
            ScrapeRequest(TARGET_URL, premium=True),
            10,
        ),
        (
            ScrapeDoProvider(token="token"),
            ScrapeRequest(TARGET_URL, premium=True, render_js=True),
            25,
        ),
        (ScrapingBeeProvider(api_key="key"), ScrapeRequest(TARGET_URL), 1),
        (
            ScrapingBeeProvider(api_key="key"),
            ScrapeRequest(TARGET_URL, premium=True, render_js=True),
            25,
        ),
        (ScraperApiProvider(api_key="key"), ScrapeRequest(TARGET_URL), 1),
        (
            ScraperApiProvider(api_key="key"),
            ScrapeRequest(TARGET_URL, render_js=True),
            10,
        ),
        (
            ScraperApiProvider(api_key="key"),
            ScrapeRequest(TARGET_URL, premium=True, render_js=True),
            25,
        ),
    ],
)
def test_core_provider_cost_estimates_match_billed_request_shape(
    provider, scrape_request, expected
):
    assert provider.estimated_cost_units(scrape_request) == expected


# ---------- RawHttpProvider ----------


class TestRawHttp:
    @respx.mock
    async def test_success(self):
        respx.get(TARGET_URL).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        result = await RawHttpProvider().scrape(ScrapeRequest(url=TARGET_URL))
        assert result.success is True
        assert result.html == GOOD_HTML
        assert result.status_code == 200
        assert result.provider == "raw_http"
        assert result.failure_reason is None

    @respx.mock
    async def test_timeout(self):
        respx.get(TARGET_URL).mock(side_effect=httpx.ReadTimeout("timed out"))
        result = await RawHttpProvider().scrape(ScrapeRequest(url=TARGET_URL))
        assert result.success is False
        assert result.failure_reason == FailureReason.TIMEOUT

    @respx.mock
    async def test_403(self):
        respx.get(TARGET_URL).mock(return_value=httpx.Response(403, text="Forbidden"))
        result = await RawHttpProvider().scrape(ScrapeRequest(url=TARGET_URL))
        assert result.success is False
        assert result.failure_reason == FailureReason.HTTP_403
        assert result.status_code == 403

    async def test_proxy_auth_retries_direct(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SCRAPE_PROXY_URL", "http://bad-proxy.example")
        calls = []

        async def fake_scrape(self, request, proxy_url, start):
            calls.append(proxy_url)
            if proxy_url:
                return result_proxy_error(request.url)
            return result_success(request.url)

        def result_proxy_error(url):
            from scrape_gateway.models import ScrapeResult

            return ScrapeResult(
                url=url,
                provider="raw_http",
                success=False,
                error="407 Proxy Authentication Required",
                failure_reason=FailureReason.PROXY_ERROR,
                route="raw_http",
            )

        def result_success(url):
            from scrape_gateway.models import ScrapeResult

            return ScrapeResult(
                url=url,
                provider="raw_http",
                success=True,
                status_code=200,
                html=GOOD_HTML,
                route="raw_http",
            )

        monkeypatch.setattr(RawHttpProvider, "_scrape", fake_scrape)
        result = await RawHttpProvider().scrape(ScrapeRequest(url=TARGET_URL))
        assert result.success is True
        assert calls == ["http://bad-proxy.example", None]
        assert result.metadata["proxy_fallback"] == "disabled_after_proxy_error"


# ---------- ScrapeDriveProvider ----------


class TestScrapeDrive:
    API_KEY = "sd_test_key_123"
    BASE = "https://sync.scrapedrive.com/api/v1/scrape"

    async def test_missing_api_key(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("SCRAPEDRIVE_API_KEY", raising=False)
        provider = ScrapeDriveProvider(api_key=None)
        result = await provider.scrape(ScrapeRequest(url=TARGET_URL))
        assert result.success is False
        assert "Missing" in (result.error or "")
        assert result.failure_reason is FailureReason.PROVIDER_UNAVAILABLE
        assert provider.availability_error() == "Missing SCRAPEDRIVE_API_KEY"

    @respx.mock
    async def test_standard_tier(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScrapeDriveProvider(api_key=self.API_KEY)
        result = await prov.scrape(ScrapeRequest(url=TARGET_URL))

        assert result.success is True
        assert result.html == GOOD_HTML
        assert result.route == "scrapedrive:standard"
        assert result.cost_units == 5
        assert result.run_cost_units == 5
        assert [entry.to_dict() for entry in result.attempt_ledger] == [
            {
                "provider": "scrapedrive",
                "route": "scrapedrive:standard",
                "cost_units": 5,
                "cost_provenance": "estimated",
                "success": True,
                "latency_ms": result.latency_ms,
                "status_code": 200,
                "failure_reason": None,
                "block_type": None,
            }
        ]

        req = route.calls[0].request
        assert req.url.params["api_key"] == self.API_KEY
        assert req.url.params["url"] == TARGET_URL
        assert req.url.params["proxy_pool"] == "datacenter"
        assert req.url.params["render_js"] == "false"
        assert "scrape_tier" not in req.url.params

    @respx.mock
    async def test_premium_maps_to_hyperdrive(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScrapeDriveProvider(api_key=self.API_KEY)
        result = await prov.scrape(ScrapeRequest(url=TARGET_URL, premium=True))

        assert result.route == "scrapedrive:hyperdrive"
        assert result.cost_units == 15

        req = route.calls[0].request
        assert req.url.params["proxy_pool"] == "residential"
        assert req.url.params["render_js"] == "true"
        assert req.url.params["wait_browser"] == "networkidle"
        assert req.url.params["block_resources"] == "false"

    @respx.mock
    async def test_country_maps_to_advanced(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScrapeDriveProvider(api_key=self.API_KEY)
        result = await prov.scrape(ScrapeRequest(url=TARGET_URL, country="us"))

        assert result.route == "scrapedrive:advanced"
        assert result.cost_units == 10

        req = route.calls[0].request
        assert req.url.params["proxy_pool"] == "residential"
        assert req.url.params["proxy_country"] == "US"
        assert "country_code" not in req.url.params

    @respx.mock
    async def test_timeout(self):
        respx.get(self.BASE).mock(side_effect=httpx.ReadTimeout("timed out"))
        prov = ScrapeDriveProvider(api_key=self.API_KEY)
        result = await prov.scrape(ScrapeRequest(url=TARGET_URL))
        assert result.success is False
        assert result.failure_reason == FailureReason.TIMEOUT

    async def test_uses_request_timeout_budget(self, monkeypatch: pytest.MonkeyPatch):
        observed = []

        class FakeClient:
            def __init__(self, *, timeout, follow_redirects):
                observed.append(timeout)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, url, params):
                return httpx.Response(200, text=GOOD_HTML, request=httpx.Request("GET", url))

        monkeypatch.setattr("scrape_gateway.providers.scrapedrive.httpx.AsyncClient", FakeClient)

        result = await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, timeout_seconds=17)
        )

        assert result.success is True
        assert observed == [17]

    async def test_timeout_budget_covers_all_tier_escalations(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        attempted_tiers = []

        async def slow_failure(self, request, tier):
            attempted_tiers.append(tier)
            await asyncio.sleep(0.02)
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                error=f"{tier} failed",
                failure_reason=FailureReason.PROVIDER_ERROR,
            )

        monkeypatch.setattr(ScrapeDriveProvider, "_attempt", slow_failure)

        result = await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, timeout_seconds=0.03)
        )

        assert result.failure_reason == FailureReason.TIMEOUT
        assert attempted_tiers == ["standard", "advanced"]
        assert result.metadata["attempted_tiers"] == ["standard", "advanced"]
        assert result.cost_units == 15
        assert result.run_cost_units == 15
        assert [entry.route for entry in result.attempt_ledger] == [
            "scrapedrive:standard",
            "scrapedrive:advanced",
        ]
        assert result.attempt_ledger[-1].failure_reason == FailureReason.TIMEOUT

    async def test_cost_budget_stops_before_unaffordable_internal_tier(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        attempted_tiers = []

        async def fail_tier(self, request, tier):
            attempted_tiers.append(tier)
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                status_code=403,
                failure_reason=FailureReason.HTTP_403,
                route=f"scrapedrive:{tier}",
            )

        monkeypatch.setattr(ScrapeDriveProvider, "_attempt", fail_tier)

        result = await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(
                url=TARGET_URL,
                metadata={"_remaining_cost_units": 8},
            )
        )

        assert attempted_tiers == ["standard"]
        assert result.success is False
        assert result.failure_reason is FailureReason.BUDGET_EXCEEDED
        assert result.run_cost_units == 5
        assert [entry.route for entry in result.attempt_ledger] == ["scrapedrive:standard"]
        assert result.metadata["budget_stop"]["next_tier"] == "advanced"
        assert result.metadata["budget_stop"]["next_attempt_cost_units"] == 10

    async def test_cost_budget_allows_internal_tier_that_exactly_fits(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        attempted_tiers = []

        async def advanced_succeeds(self, request, tier):
            attempted_tiers.append(tier)
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=tier == "advanced",
                status_code=200 if tier == "advanced" else 403,
                html=GOOD_HTML if tier == "advanced" else None,
                failure_reason=None if tier == "advanced" else FailureReason.HTTP_403,
                route=f"scrapedrive:{tier}",
            )

        monkeypatch.setattr(ScrapeDriveProvider, "_attempt", advanced_succeeds)

        result = await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(
                url=TARGET_URL,
                metadata={"_remaining_cost_units": 15},
            )
        )

        assert result.success is True
        assert attempted_tiers == ["standard", "advanced"]
        assert result.run_cost_units == 15

    @pytest.mark.parametrize(
        ("scrape_request", "expected"),
        [
            (ScrapeRequest(TARGET_URL), 5),
            (ScrapeRequest(TARGET_URL, country="US"), 10),
            (ScrapeRequest(TARGET_URL, premium=True), 15),
            (ScrapeRequest(TARGET_URL, render_js=True), 10),
            (ScrapeRequest(TARGET_URL, screenshot=True), 15),
            (ScrapeRequest(TARGET_URL, country="US", render_js=True), 15),
            (ScrapeRequest(TARGET_URL, premium=True, screenshot=True), 20),
        ],
    )
    def test_estimates_next_tier_cost(self, scrape_request, expected):
        assert (
            ScrapeDriveProvider(api_key=self.API_KEY).estimated_cost_units(scrape_request)
            == expected
        )

    @respx.mock
    async def test_escalation_success_charges_every_started_tier(self):
        route = respx.get(self.BASE).mock(
            side_effect=[
                httpx.Response(403, text="Forbidden"),
                httpx.Response(403, text="Forbidden"),
                httpx.Response(200, text=GOOD_HTML),
            ]
        )

        result = await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL)
        )

        assert result.success is True
        assert result.route == "scrapedrive:hyperdrive"
        assert result.cost_units == 30
        assert result.run_cost_units == 30
        assert [entry.route for entry in result.attempt_ledger] == [
            "scrapedrive:standard",
            "scrapedrive:advanced",
            "scrapedrive:hyperdrive",
        ]
        assert [entry.cost_units for entry in result.attempt_ledger] == [5, 10, 15]
        assert [entry.success for entry in result.attempt_ledger] == [False, False, True]
        assert len(route.calls) == 3

    @pytest.mark.parametrize("status", [401, 402, 422, 429])
    @respx.mock
    async def test_uncharged_rejections_record_zero_cost(self, status):
        respx.get(self.BASE).mock(
            return_value=httpx.Response(status, text='{"detail": "rejected"}')
        )

        result = await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, metadata={"start_tier": "scrapedrive:hyperdrive"})
        )

        assert result.success is False
        assert result.cost_units == 0
        assert result.run_cost_units == 0
        assert [entry.cost_units for entry in result.attempt_ledger] == [0]
        assert all(entry.cost_provenance == "estimated" for entry in result.attempt_ledger)

    @respx.mock
    async def test_all_tier_failure_charges_every_started_tier(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(403, text="Forbidden"))

        result = await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL)
        )

        assert result.success is False
        assert result.cost_units == 30
        assert result.run_cost_units == 30
        assert [entry.cost_units for entry in result.attempt_ledger] == [5, 10, 15]
        assert all(entry.success is False for entry in result.attempt_ledger)
        assert len(route.calls) == 3

    @respx.mock
    async def test_json_response(self):
        import json

        body = json.dumps({"html": GOOD_HTML, "markdown": "# Hello"})
        respx.get(self.BASE).mock(
            return_value=httpx.Response(
                200,
                text=body,
                headers={"content-type": "application/json"},
            )
        )
        prov = ScrapeDriveProvider(api_key=self.API_KEY)
        result = await prov.scrape(ScrapeRequest(url=TARGET_URL))

        assert result.success is True
        assert result.html == GOOD_HTML
        assert result.markdown == "# Hello"

    @respx.mock
    async def test_downloads_requested_screenshot_evidence(self):
        import json

        screenshot_url = "https://assets.scrapedrive.test/run-123.png"
        body = json.dumps({"html": GOOD_HTML, "screenshot_url": screenshot_url})
        respx.get(self.BASE).mock(
            return_value=httpx.Response(
                200,
                text=body,
                headers={"content-type": "application/json"},
            )
        )
        screenshot = b"\x89PNG\r\n\x1a\nvisual-evidence"
        screenshot_route = respx.get(screenshot_url).mock(
            return_value=httpx.Response(
                200,
                content=screenshot,
                headers={"content-type": "image/png"},
            )
        )

        result = await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, screenshot=True, premium=True)
        )

        assert result.success is True
        assert result.screenshot == screenshot
        assert result.metadata["screenshot_url"] == screenshot_url
        assert len(screenshot_route.calls) == 1

    @respx.mock
    async def test_retries_a_screenshot_that_has_not_landed_yet(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        import json

        monkeypatch.setattr(
            "scrape_gateway.providers.scrapedrive.SCREENSHOT_RETRY_DELAY_SECONDS", 0
        )
        screenshot_url = "https://assets.scrapedrive.test/run-late.png"
        respx.get(self.BASE).mock(
            return_value=httpx.Response(
                200,
                text=json.dumps({"html": GOOD_HTML, "screenshot_url": screenshot_url}),
                headers={"content-type": "application/json"},
            )
        )
        screenshot = b"\x89PNG\r\n\x1a\nvisual-evidence"
        # The object store answers 403 for a key it has not finished writing.
        screenshot_route = respx.get(screenshot_url).mock(
            side_effect=[
                httpx.Response(403, text="<Error/>", headers={"content-type": "application/xml"}),
                httpx.Response(200, content=screenshot, headers={"content-type": "image/png"}),
            ]
        )

        result = await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, screenshot=True)
        )

        assert result.success is True
        assert result.screenshot == screenshot
        assert len(screenshot_route.calls) == 2

    @respx.mock
    async def test_gives_up_on_a_screenshot_that_never_lands(self, monkeypatch: pytest.MonkeyPatch):
        import json

        monkeypatch.setattr(
            "scrape_gateway.providers.scrapedrive.SCREENSHOT_RETRY_DELAY_SECONDS", 0
        )
        screenshot_url = "https://assets.scrapedrive.test/run-missing.png"
        respx.get(self.BASE).mock(
            return_value=httpx.Response(
                200,
                text=json.dumps({"html": GOOD_HTML, "screenshot_url": screenshot_url}),
                headers={"content-type": "application/json"},
            )
        )
        screenshot_route = respx.get(screenshot_url).mock(
            return_value=httpx.Response(
                403, text="<Error/>", headers={"content-type": "application/xml"}
            )
        )

        result = await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(
                url=TARGET_URL, screenshot=True, metadata={"start_tier": "scrapedrive:hyperdrive"}
            )
        )

        assert result.success is False
        assert "403" in (result.error or "")
        assert len(screenshot_route.calls) == 3

    @respx.mock
    async def test_rejects_success_without_requested_screenshot_evidence(self):
        import json

        respx.get(self.BASE).mock(
            return_value=httpx.Response(
                200,
                text=json.dumps({"html": GOOD_HTML}),
                headers={"content-type": "application/json"},
            )
        )

        result = await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, screenshot=True, premium=True)
        )

        assert result.success is False
        assert result.failure_reason == FailureReason.PROVIDER_ERROR
        assert "screenshot" in (result.error or "").lower()

    @respx.mock
    async def test_respects_start_tier(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScrapeDriveProvider(api_key=self.API_KEY)
        request = ScrapeRequest(url=TARGET_URL, metadata={"start_tier": "scrapedrive:advanced"})
        await prov.scrape(request)

        called_params = dict(route.calls[0].request.url.params)
        assert called_params["proxy_pool"] == "residential"
        assert "scrape_tier" not in called_params

    @respx.mock
    async def test_start_tier_hyperdrive(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScrapeDriveProvider(api_key=self.API_KEY)
        request = ScrapeRequest(url=TARGET_URL, metadata={"start_tier": "scrapedrive:hyperdrive"})
        await prov.scrape(request)

        called_params = dict(route.calls[0].request.url.params)
        assert called_params["proxy_pool"] == "residential"
        assert called_params["render_js"] == "true"
        assert called_params["wait_browser"] == "networkidle"
        assert called_params["block_resources"] == "false"

    @respx.mock
    async def test_ignores_irrelevant_start_tier(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScrapeDriveProvider(api_key=self.API_KEY)
        request = ScrapeRequest(url=TARGET_URL, metadata={"start_tier": "scraperapi:premium"})
        await prov.scrape(request)

        called_params = dict(route.calls[0].request.url.params)
        assert called_params["proxy_pool"] == "datacenter"

    @respx.mock
    async def test_no_removed_fields_transmitted(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScrapeDriveProvider(api_key=self.API_KEY)
        request = ScrapeRequest(
            url=TARGET_URL,
            country="us",
            render_js=True,
            wait_selector="#product",
            extra_wait_ms=1500,
        )
        await prov.scrape(request)

        called_params = dict(route.calls[0].request.url.params)
        for removed in ("scrape_tier", "country_code", "wait_for_selector", "extra_wait"):
            assert removed not in called_params

    @respx.mock
    async def test_wait_fields_map_to_current_names(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScrapeDriveProvider(api_key=self.API_KEY)
        request = ScrapeRequest(
            url=TARGET_URL,
            render_js=True,
            wait_selector="#product",
            extra_wait_ms=1500,
            block_ads=True,
        )
        await prov.scrape(request)

        called_params = dict(route.calls[0].request.url.params)
        assert called_params["wait_for"] == "#product"
        assert called_params["wait_ms"] == "1500"
        assert called_params["block_ads"] == "true"

    @respx.mock
    async def test_wait_ms_is_clamped_to_spec_bound(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScrapeDriveProvider(api_key=self.API_KEY)
        request = ScrapeRequest(url=TARGET_URL, render_js=True, extra_wait_ms=999_999)
        await prov.scrape(request)

        called_params = dict(route.calls[0].request.url.params)
        assert called_params["wait_ms"] == "30000"

    @respx.mock
    async def test_block_ads_defaults_to_false_explicitly(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScrapeDriveProvider(api_key=self.API_KEY)
        await prov.scrape(ScrapeRequest(url=TARGET_URL, render_js=True))

        called_params = dict(route.calls[0].request.url.params)
        assert called_params["block_ads"] == "false"

    @respx.mock
    async def test_block_ads_is_not_sent_without_a_browser(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScrapeDriveProvider(api_key=self.API_KEY)
        await prov.scrape(ScrapeRequest(url=TARGET_URL, block_ads=True))

        called_params = dict(route.calls[0].request.url.params)
        assert called_params["render_js"] == "false"
        # block_ads and block_resources are browser-only per the spec.
        assert "block_ads" not in called_params
        assert "block_resources" not in called_params

    @respx.mock
    async def test_markdown_output_asks_the_api_to_convert_it(self):
        markdown = (
            "# Test\n\nA realistic markdown page with enough content to pass the "
            "minimum length checks that guard against an empty result."
        )
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=markdown))
        prov = ScrapeDriveProvider(api_key=self.API_KEY)
        result = await prov.scrape(ScrapeRequest(url=TARGET_URL, output_format="markdown"))

        assert route.calls[0].request.url.params["result_type"] == "page_markdown"
        assert result.success is True
        # A successful sync scrape replays the target's text/html content-type even for
        # markdown, so the asked-for format is the only thing that can label the body.
        assert result.markdown == markdown
        assert result.html == markdown

    @respx.mock
    async def test_html_output_leaves_markdown_empty(self):
        respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        result = await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL)
        )

        assert result.html == GOOD_HTML
        assert result.markdown is None

    @respx.mock
    async def test_a_failed_markdown_scrape_reports_no_markdown(self):
        respx.get(self.BASE).mock(return_value=httpx.Response(403, text="Forbidden"))
        result = await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, output_format="markdown")
        )

        assert result.success is False
        assert result.markdown is None

    @pytest.mark.parametrize(
        ("timeout_seconds", "expected"),
        [
            (45, "45000"),
            (5, "10000"),  # below the spec minimum
            (300, "120000"),  # above the sync ceiling
        ],
    )
    @respx.mock
    async def test_timeout_ms_is_sent_within_the_spec_bounds(self, timeout_seconds, expected):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, timeout_seconds=timeout_seconds)
        )

        assert route.calls[0].request.url.params["timeout_ms"] == expected

    @respx.mock
    async def test_job_id_is_recorded_for_support(self):
        respx.get(self.BASE).mock(
            return_value=httpx.Response(
                200, text=GOOD_HTML, headers={"x-sdrive-job-id": "01M0FQRX2ZEDD9QPAM2BW0099Y"}
            )
        )
        result = await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL)
        )

        assert result.metadata["job_id"] == "01M0FQRX2ZEDD9QPAM2BW0099Y"

    @respx.mock
    async def test_base_url_is_configurable(self):
        base = "https://sync.scrapedrive.test/api/v1/scrape"
        route = respx.get(base).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        result = await ScrapeDriveProvider(api_key=self.API_KEY, base_url=base).scrape(
            ScrapeRequest(url=TARGET_URL)
        )

        assert result.success is True
        assert len(route.calls) == 1


# ---------- ScrapeDoProvider ----------


class TestScrapeDo:
    TOKEN = "sd_token_456"
    BASE = "https://api.scrape.do/"

    async def test_missing_token(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("SCRAPE_DO_TOKEN", raising=False)
        result = await ScrapeDoProvider(token=None).scrape(ScrapeRequest(url=TARGET_URL))
        assert result.success is False
        assert "Missing" in (result.error or "")
        assert result.failure_reason is FailureReason.PROVIDER_UNAVAILABLE

    @respx.mock
    async def test_success(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScrapeDoProvider(token=self.TOKEN)
        result = await prov.scrape(ScrapeRequest(url=TARGET_URL))

        assert result.success is True
        assert result.html == GOOD_HTML
        assert result.provider == "scrape_do"

        req = route.calls[0].request
        assert req.url.params["token"] == self.TOKEN
        assert req.url.params["url"] == TARGET_URL

    @respx.mock
    async def test_params_country_premium_render(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScrapeDoProvider(token=self.TOKEN)
        req_in = ScrapeRequest(url=TARGET_URL, country="de", premium=True, render_js=True)
        result = await prov.scrape(req_in)

        assert result.route == "scrape_do:super"
        assert result.cost_units == 25

        req = route.calls[0].request
        assert req.url.params["geoCode"] == "de"
        assert req.url.params["super"] == "true"
        assert req.url.params["render"] == "true"

    @respx.mock
    async def test_timeout(self):
        respx.get(self.BASE).mock(side_effect=httpx.ReadTimeout("timed out"))
        prov = ScrapeDoProvider(token=self.TOKEN)
        result = await prov.scrape(ScrapeRequest(url=TARGET_URL))
        assert result.success is False
        assert result.failure_reason == FailureReason.TIMEOUT


# ---------- ScrapingBeeProvider ----------


class TestScrapingBee:
    API_KEY = "sb_key_789"
    BASE = "https://app.scrapingbee.com/api/v1/"

    async def test_missing_api_key(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("SCRAPINGBEE_API_KEY", raising=False)
        result = await ScrapingBeeProvider(api_key=None).scrape(ScrapeRequest(url=TARGET_URL))
        assert result.success is False
        assert "Missing" in (result.error or "")
        assert result.failure_reason is FailureReason.PROVIDER_UNAVAILABLE

    @respx.mock
    async def test_invalid_api_key_is_provider_unavailable(self):
        respx.get(self.BASE).mock(
            return_value=httpx.Response(401, text="Authentication failed: invalid API key")
        )

        result = await ScrapingBeeProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL)
        )

        assert result.success is False
        assert result.failure_reason is FailureReason.PROVIDER_UNAVAILABLE

    @respx.mock
    async def test_success(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScrapingBeeProvider(api_key=self.API_KEY)
        result = await prov.scrape(ScrapeRequest(url=TARGET_URL))

        assert result.success is True
        assert result.html == GOOD_HTML
        assert result.provider == "scrapingbee"

        req = route.calls[0].request
        assert req.url.params["api_key"] == self.API_KEY
        assert req.url.params["url"] == TARGET_URL
        assert req.url.params["render_js"] == "false"

    @respx.mock
    async def test_params_country_premium(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScrapingBeeProvider(api_key=self.API_KEY)
        req_in = ScrapeRequest(url=TARGET_URL, country="fr", premium=True, render_js=True)
        result = await prov.scrape(req_in)

        assert result.route == "scrapingbee:premium"
        assert result.cost_units == 25

        req = route.calls[0].request
        assert req.url.params["country_code"] == "fr"
        assert req.url.params["premium_proxy"] == "true"
        assert req.url.params["render_js"] == "true"

    @respx.mock
    async def test_timeout(self):
        respx.get(self.BASE).mock(side_effect=httpx.ReadTimeout("timed out"))
        prov = ScrapingBeeProvider(api_key=self.API_KEY)
        result = await prov.scrape(ScrapeRequest(url=TARGET_URL))
        assert result.success is False
        assert result.failure_reason == FailureReason.TIMEOUT


# ---------- ScraperApiProvider ----------


class TestScraperApi:
    API_KEY = "sa_key_101"
    BASE = "https://api.scraperapi.com/"

    async def test_missing_api_key(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("SCRAPERAPI_API_KEY", raising=False)
        result = await ScraperApiProvider(api_key=None).scrape(ScrapeRequest(url=TARGET_URL))
        assert result.success is False
        assert "Missing" in (result.error or "")
        assert result.failure_reason is FailureReason.PROVIDER_UNAVAILABLE

    @respx.mock
    async def test_success(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScraperApiProvider(api_key=self.API_KEY)
        result = await prov.scrape(ScrapeRequest(url=TARGET_URL))

        assert result.success is True
        assert result.html == GOOD_HTML
        assert result.provider == "scraperapi"

        req = route.calls[0].request
        assert req.url.params["api_key"] == self.API_KEY
        assert req.url.params["url"] == TARGET_URL

    @respx.mock
    async def test_params(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScraperApiProvider(api_key=self.API_KEY)
        req_in = ScrapeRequest(
            url=TARGET_URL, country="gb", premium=True, render_js=True, screenshot=False
        )
        result = await prov.scrape(req_in)

        assert result.route == "scraperapi:premium"
        assert result.cost_units == 25

        req = route.calls[0].request
        assert req.url.params["country_code"] == "gb"
        assert req.url.params["premium"] == "true"
        assert req.url.params["render"] == "true"

    @respx.mock
    async def test_screenshot_response(self):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        respx.get(self.BASE).mock(
            return_value=httpx.Response(
                200,
                content=png_bytes,
                headers={"content-type": "image/png"},
            )
        )
        prov = ScraperApiProvider(api_key=self.API_KEY)
        result = await prov.scrape(ScrapeRequest(url=TARGET_URL, screenshot=True))

        assert result.success is True
        assert result.screenshot == png_bytes
        assert result.html is None
        assert result.failure_reason is None

    @respx.mock
    async def test_screenshot_request_rejects_non_image_response(self):
        respx.get(self.BASE).mock(
            return_value=httpx.Response(
                200,
                text=GOOD_HTML,
                headers={"content-type": "text/html"},
            )
        )

        result = await ScraperApiProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, screenshot=True)
        )

        assert result.success is False
        assert result.screenshot is None
        assert result.html == GOOD_HTML
        assert result.failure_reason is FailureReason.PROVIDER_ERROR
        assert result.error == "Screenshot was requested but ScraperAPI returned text/html"

    @respx.mock
    async def test_timeout(self):
        respx.get(self.BASE).mock(side_effect=httpx.ReadTimeout("timed out"))
        prov = ScraperApiProvider(api_key=self.API_KEY)
        result = await prov.scrape(ScrapeRequest(url=TARGET_URL))
        assert result.success is False
        assert result.failure_reason == FailureReason.TIMEOUT


class TestCapabilityGuards:
    """`can_handle` is the only thing standing between a request that asks for a
    capability and a provider that cannot deliver it. A guard that is missing here
    does not raise; the request is accepted and the option is silently discarded."""

    class HtmlOnly(RawHttpProvider):
        name = "html_only"
        capabilities = frozenset({"html"})

    class FullyCapable(RawHttpProvider):
        name = "fully_capable"
        capabilities = frozenset({"html", "render_js", "premium", "screenshot", "country"})

    @pytest.mark.parametrize(
        ("kwargs", "capability"),
        [
            ({"render_js": True}, "render_js"),
            ({"premium": True}, "premium"),
            ({"screenshot": True}, "screenshot"),
            ({"country": "RO"}, "country"),
        ],
    )
    def test_provider_lacking_the_capability_is_rejected(self, kwargs, capability):
        request = ScrapeRequest(url=TARGET_URL, **kwargs)

        assert self.HtmlOnly().can_handle(request) is False, (
            f"a provider without {capability!r} accepted a request that needs it, "
            "so the option would be dropped without an error"
        )
        assert self.FullyCapable().can_handle(request) is True

    def test_plain_request_is_handled_by_the_least_capable_provider(self):
        assert self.HtmlOnly().can_handle(ScrapeRequest(url=TARGET_URL)) is True

    def test_empty_country_does_not_restrict_routing(self):
        """`country=None` and `country=""` mean "no preference", not "any country"."""
        for value in (None, ""):
            request = ScrapeRequest(url=TARGET_URL, country=value)
            assert self.HtmlOnly().can_handle(request) is True
