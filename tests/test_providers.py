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

            async def get(self, url, params, headers=None):
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
            (120, "120000"),  # exactly the sync ceiling; past it the job goes async
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
    async def test_a_block_arrives_as_the_target_saw_it(self):
        route = respx.get(self.BASE).mock(
            return_value=httpx.Response(
                403, text="<html><body>Please enable JS</body></html>", request=None
            )
        )
        result = await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, metadata={"start_tier": "scrapedrive:hyperdrive"})
        )

        # transparent_mode is what turns ScrapeDrive's own 500 JSON error into the
        # target's real 403 and challenge page, so a block reads as a block
        # instead of as provider breakage.
        assert route.calls[0].request.url.params["transparent_mode"] == "true"
        assert result.status_code == 403
        assert result.failure_reason is FailureReason.HTTP_403

    @respx.mock
    async def test_transparent_mode_is_not_sent_on_an_async_job(self):
        submit = respx.post("https://api.scrapedrive.com:8443/api/v1/scrape/async").mock(
            return_value=httpx.Response(
                202,
                json={
                    "id": "01JOB",
                    "status": "queued",
                    "status_url": "https://api.scrapedrive.com:8443/api/v1/job/01JOB",
                },
            )
        )
        respx.get("https://api.scrapedrive.com:8443/api/v1/job/01JOB").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "01JOB",
                    "status": "completed",
                    "response": {
                        "status_code": 200,
                        "final_url": TARGET_URL,
                        "headers": {},
                        "body": GOOD_HTML,
                        "credits": 5,
                    },
                },
            )
        )

        await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, timeout_seconds=300)
        )

        import json as jsonlib

        # The spec restricts it to sync mode.
        assert "transparent_mode" not in jsonlib.loads(submit.calls[0].request.content)

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


class TestScrapeDriveAsync:
    """Anything past the 120s sync ceiling has to go to the async host."""

    API_KEY = "sd_test_key_123"
    SUBMIT = "https://api.scrapedrive.com:8443/api/v1/scrape/async"
    JOB = "https://api.scrapedrive.com:8443/api/v1/job/01JOB"
    LONG = 300.0

    def _envelope(self):
        return httpx.Response(202, json={"id": "01JOB", "status": "queued", "status_url": self.JOB})

    def _finished(self, **overrides):
        response = {
            "status_code": 200,
            "final_url": TARGET_URL,
            "headers": {"content-type": "text/html"},
            "body": GOOD_HTML,
            "credits": 5,
        }
        response.update(overrides)
        return httpx.Response(
            200, json={"id": "01JOB", "status": "completed", "response": response}
        )

    @respx.mock
    async def test_a_long_job_is_submitted_and_polled(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("scrape_gateway.providers.scrapedrive.POLL_INTERVAL_SECONDS", 0)
        submit = respx.post(self.SUBMIT).mock(return_value=self._envelope())
        poll = respx.get(self.JOB).mock(
            side_effect=[
                # Real in-flight words seen from the API: queued, processing,
                # active. None of them is worth an allow-list — the absence of a
                # response is what says the job is still going.
                httpx.Response(200, json={"id": "01JOB", "status": "queued"}),
                httpx.Response(200, json={"id": "01JOB", "status": "active"}),
                self._finished(),
            ]
        )

        result = await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, timeout_seconds=self.LONG)
        )

        assert result.success is True
        assert result.html == GOOD_HTML
        assert len(submit.calls) == 1
        assert len(poll.calls) == 3
        assert result.metadata["mode"] == "async"

    @respx.mock
    async def test_a_short_job_never_touches_the_async_host(self):
        sync = respx.get("https://sync.scrapedrive.com/api/v1/scrape").mock(
            return_value=httpx.Response(200, text=GOOD_HTML)
        )
        submit = respx.post(self.SUBMIT).mock(return_value=self._envelope())

        await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, timeout_seconds=45)
        )

        assert len(sync.calls) == 1
        assert len(submit.calls) == 0

    @respx.mock
    async def test_the_job_is_sent_as_json_with_native_types(self):
        submit = respx.post(self.SUBMIT).mock(return_value=self._envelope())
        respx.get(self.JOB).mock(return_value=self._finished())

        await ScrapeDriveProvider(api_key=self.API_KEY, auto=True).scrape(
            ScrapeRequest(url=TARGET_URL, timeout_seconds=self.LONG, render_js=True)
        )

        import json as jsonlib

        body = jsonlib.loads(submit.calls[0].request.content)
        # The async host rejects an Auto job whose max_credits arrives as a query
        # string, so these have to be real JSON types, not "true"/"15".
        assert body["auto"] is True
        assert body["max_credits"] == 15
        assert body["render_js"] is True
        # Async raises the ceiling from 120s to the spec's 130s maximum.
        assert body["timeout_ms"] == 130000

    @respx.mock
    async def test_reported_credits_are_billed_exactly(self):
        respx.post(self.SUBMIT).mock(return_value=self._envelope())
        respx.get(self.JOB).mock(return_value=self._finished(credits=15))

        result = await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, timeout_seconds=self.LONG, screenshot=False)
        )

        # A finished job states its price, so this is the one ScrapeDrive path
        # that does not have to guess.
        assert result.metadata["cost_provenance"] == "exact"
        assert result.attempt_ledger[-1].cost_provenance == "exact"
        assert result.attempt_ledger[-1].cost_units == 15
        assert result.run_cost_units == 15

    @respx.mock
    async def test_an_unreachable_target_is_a_failure_despite_completed(self):
        respx.post(self.SUBMIT).mock(return_value=self._envelope())
        respx.get(self.JOB).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "01JOB",
                    # The job completed. The scrape did not.
                    "status": "completed",
                    "response": {
                        "status_code": 0,
                        "final_url": TARGET_URL,
                        "headers": {},
                        "body": "",
                        "credits": 0,
                    },
                    "reason": "The URL could not be reached.",
                },
            )
        )

        result = await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, timeout_seconds=self.LONG)
        )

        assert result.success is False
        assert "could not be reached" in (result.error or "")
        # Nothing was charged, and the job said so.
        assert result.run_cost_units == 0
        assert result.attempt_ledger[-1].cost_provenance == "exact"

    @respx.mock
    async def test_an_async_screenshot_comes_from_the_replayed_headers(self):
        screenshot_url = "https://assets.scrapedrive.test/job.jpg"
        respx.post(self.SUBMIT).mock(return_value=self._envelope())
        respx.get(self.JOB).mock(
            return_value=self._finished(
                credits=15,
                headers={"content-type": "text/html", "x-sdrive-screenshot-url": screenshot_url},
            )
        )
        image = b"\x89PNG\r\n\x1a\nvisual-evidence"
        respx.get(screenshot_url).mock(
            return_value=httpx.Response(200, content=image, headers={"content-type": "image/png"})
        )

        result = await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, timeout_seconds=self.LONG, screenshot=True)
        )

        assert result.success is True
        assert result.screenshot == image

    @respx.mock
    async def test_a_refused_submit_costs_nothing(self):
        respx.post(self.SUBMIT).mock(
            return_value=httpx.Response(422, json={"error": "Validation failed"})
        )

        result = await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, timeout_seconds=self.LONG)
        )

        assert result.success is False
        assert result.run_cost_units == 0


class TestScrapeDriveHeaders:
    """The adapter took a headers dict and threw it away."""

    API_KEY = "sd_test_key_123"
    BASE = "https://sync.scrapedrive.com/api/v1/scrape"

    @respx.mock
    async def test_caller_headers_are_forwarded_with_the_prefix(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, headers={"Authorization": "Bearer token"})
        )

        sent = route.calls[0].request
        # ScrapeDrive strips the prefix before passing the header to the target.
        assert sent.headers["sdrive-Authorization"] == "Bearer token"
        assert sent.url.params["forward_sdrive_headers"] == "true"

    @respx.mock
    async def test_a_referer_travels_as_a_header(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, referer="https://news.example/story")
        )

        assert route.calls[0].request.headers["sdrive-Referer"] == "https://news.example/story"

    @respx.mock
    async def test_a_sync_scrape_says_the_headers_will_be_dropped(self, capsys):
        respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, headers={"Authorization": "Bearer token"})
        )

        # The sync host accepts forward_sdrive_headers and ignores it. Losing an
        # Authorization header without a word is how a caller ends up debugging
        # the wrong thing.
        warning = capsys.readouterr().err
        assert "does not forward sdrive- headers" in warning

    @respx.mock
    async def test_a_long_job_does_not_warn_because_async_forwards_them(self, capsys):
        respx.post("https://api.scrapedrive.com:8443/api/v1/scrape/async").mock(
            return_value=httpx.Response(
                202,
                json={
                    "id": "01JOB",
                    "status": "queued",
                    "status_url": "https://api.scrapedrive.com:8443/api/v1/job/01JOB",
                },
            )
        )
        respx.get("https://api.scrapedrive.com:8443/api/v1/job/01JOB").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "01JOB",
                    "status": "completed",
                    "response": {
                        "status_code": 200,
                        "final_url": TARGET_URL,
                        "headers": {},
                        "body": GOOD_HTML,
                        "credits": 5,
                    },
                },
            )
        )

        await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(
                url=TARGET_URL, headers={"Authorization": "Bearer token"}, timeout_seconds=300
            )
        )

        assert "does not forward" not in capsys.readouterr().err

    @respx.mock
    async def test_nothing_is_forwarded_when_the_caller_set_nothing(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        await ScrapeDriveProvider(api_key=self.API_KEY).scrape(ScrapeRequest(url=TARGET_URL))

        sent = route.calls[0].request
        assert "forward_sdrive_headers" not in sent.url.params
        assert not [name for name in sent.headers if name.lower().startswith("sdrive-")]

    @respx.mock
    async def test_an_empty_referer_means_send_none(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        # "" is the request model's way of saying no referer at all, as distinct
        # from None, which means let the provider decide.
        await ScrapeDriveProvider(api_key=self.API_KEY).scrape(
            ScrapeRequest(url=TARGET_URL, referer="")
        )

        assert "sdrive-Referer" not in route.calls[0].request.headers


class TestScrapeDriveAuto:
    """ScrapeDrive's own progressive escalation, opt-in via SCRAPEDRIVE_AUTO."""

    API_KEY = "sd_test_key_123"
    BASE = "https://sync.scrapedrive.com/api/v1/scrape"

    def provider(self):
        return ScrapeDriveProvider(api_key=self.API_KEY, auto=True)

    def test_off_unless_the_environment_asks_for_it(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("SCRAPEDRIVE_AUTO", raising=False)
        assert ScrapeDriveProvider(api_key=self.API_KEY).auto is False
        monkeypatch.setenv("SCRAPEDRIVE_AUTO", "true")
        assert ScrapeDriveProvider(api_key=self.API_KEY).auto is True

    @respx.mock
    async def test_one_call_replaces_the_whole_ladder(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        result = await self.provider().scrape(ScrapeRequest(url=TARGET_URL))

        assert len(route.calls) == 1
        called_params = dict(route.calls[0].request.url.params)
        assert called_params["auto"] == "true"
        # The ceiling is the residential browser the manual ladder tops out at, but it
        # is reserved once instead of being paid for at every rung.
        assert called_params["max_credits"] == "15"
        for routing_field in ("proxy_pool", "proxy_country", "session_number", "custom_proxy"):
            assert routing_field not in called_params
        assert result.route == "scrapedrive:auto"
        assert result.metadata["max_credits"] == 15

    @respx.mock
    async def test_a_failure_is_not_retried_by_the_ladder(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(403, text="Forbidden"))
        result = await self.provider().scrape(ScrapeRequest(url=TARGET_URL))

        assert result.success is False
        assert len(route.calls) == 1
        assert [entry.route for entry in result.attempt_ledger] == ["scrapedrive:auto"]
        assert result.run_cost_units == 15

    @respx.mock
    async def test_a_country_request_stays_on_the_manual_ladder(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        result = await self.provider().scrape(ScrapeRequest(url=TARGET_URL, country="us"))

        called_params = dict(route.calls[0].request.url.params)
        # Auto refuses proxy_country, so a geo-targeted request cannot use it.
        assert "auto" not in called_params
        assert called_params["proxy_country"] == "US"
        assert result.route == "scrapedrive:advanced"

    @respx.mock
    async def test_max_credits_is_clipped_to_the_remaining_budget(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        result = await self.provider().scrape(
            ScrapeRequest(url=TARGET_URL, metadata={"_remaining_cost_units": 12.5})
        )

        # max_credits is an integer, so a fractional remainder rounds down.
        assert route.calls[0].request.url.params["max_credits"] == "12"
        assert result.run_cost_units == 12

    @respx.mock
    async def test_a_budget_below_the_floor_stops_before_the_call(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        result = await self.provider().scrape(
            ScrapeRequest(
                url=TARGET_URL,
                screenshot=True,
                metadata={"_remaining_cost_units": 10},
            )
        )

        # A screenshot floors the job at 15 credits, so 10 cannot buy the cheapest
        # configuration Auto is allowed to start from.
        assert len(route.calls) == 0
        assert result.failure_reason is FailureReason.BUDGET_EXCEEDED
        assert result.metadata["budget_stop"]["next_attempt_cost_units"] == 15

    @pytest.mark.parametrize(
        ("scrape_request", "expected"),
        [
            (ScrapeRequest(TARGET_URL), 15),
            (ScrapeRequest(TARGET_URL, render_js=True), 15),
            (ScrapeRequest(TARGET_URL, screenshot=True), 20),
            (ScrapeRequest(TARGET_URL, metadata={"_remaining_cost_units": 8}), 8),
            # A screenshot floors the job at 15, so a remainder of 8 reports the floor
            # and the caller refuses the provider rather than sending a doomed request.
            (ScrapeRequest(TARGET_URL, screenshot=True, metadata={"_remaining_cost_units": 8}), 15),
        ],
    )
    def test_estimate_is_the_ceiling_it_will_reserve(self, scrape_request, expected):
        assert (
            ScrapeDriveProvider(api_key=self.API_KEY, auto=True).estimated_cost_units(
                scrape_request
            )
            == expected
        )

    @respx.mock
    async def test_the_caller_shape_still_reaches_the_wire(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        await self.provider().scrape(
            ScrapeRequest(
                url=TARGET_URL,
                render_js=True,
                wait_selector="#product",
                mobile=True,
                output_format="markdown",
            )
        )

        called_params = dict(route.calls[0].request.url.params)
        assert called_params["render_js"] == "true"
        assert called_params["wait_for"] == "#product"
        assert called_params["device_type"] == "mobile"
        assert called_params["result_type"] == "page_markdown"


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
