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
        result = await ScrapeDriveProvider(api_key=None).scrape(ScrapeRequest(url=TARGET_URL))
        assert result.success is False
        assert "Missing" in (result.error or "")

    @respx.mock
    async def test_standard_tier(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScrapeDriveProvider(api_key=self.API_KEY)
        result = await prov.scrape(ScrapeRequest(url=TARGET_URL))

        assert result.success is True
        assert result.html == GOOD_HTML
        assert result.route == "scrapedrive:standard"
        assert result.cost_units == 1

        req = route.calls[0].request
        assert req.url.params["api_key"] == self.API_KEY
        assert req.url.params["url"] == TARGET_URL
        assert req.url.params["scrape_tier"] == "standard"

    @respx.mock
    async def test_premium_maps_to_hyperdrive(self):
        respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScrapeDriveProvider(api_key=self.API_KEY)
        result = await prov.scrape(ScrapeRequest(url=TARGET_URL, premium=True))

        assert result.route == "scrapedrive:hyperdrive"
        assert result.cost_units == 25

    @respx.mock
    async def test_country_maps_to_advanced(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScrapeDriveProvider(api_key=self.API_KEY)
        result = await prov.scrape(ScrapeRequest(url=TARGET_URL, country="us"))

        assert result.route == "scrapedrive:advanced"
        assert result.cost_units == 5

        req = route.calls[0].request
        assert req.url.params["country_code"] == "US"

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
        assert "hyperdrive" not in attempted_tiers

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
        assert called_params["scrape_tier"] == "advanced"

    @respx.mock
    async def test_start_tier_hyperdrive(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScrapeDriveProvider(api_key=self.API_KEY)
        request = ScrapeRequest(url=TARGET_URL, metadata={"start_tier": "scrapedrive:hyperdrive"})
        await prov.scrape(request)

        called_params = dict(route.calls[0].request.url.params)
        assert called_params["scrape_tier"] == "hyperdrive"

    @respx.mock
    async def test_ignores_irrelevant_start_tier(self):
        route = respx.get(self.BASE).mock(return_value=httpx.Response(200, text=GOOD_HTML))
        prov = ScrapeDriveProvider(api_key=self.API_KEY)
        request = ScrapeRequest(url=TARGET_URL, metadata={"start_tier": "scraperapi:premium"})
        await prov.scrape(request)

        called_params = dict(route.calls[0].request.url.params)
        assert called_params["scrape_tier"] == "standard"


# ---------- ScrapeDoProvider ----------


class TestScrapeDo:
    TOKEN = "sd_token_456"
    BASE = "https://api.scrape.do/"

    async def test_missing_token(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("SCRAPE_DO_TOKEN", raising=False)
        result = await ScrapeDoProvider(token=None).scrape(ScrapeRequest(url=TARGET_URL))
        assert result.success is False
        assert "Missing" in (result.error or "")

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
    async def test_timeout(self):
        respx.get(self.BASE).mock(side_effect=httpx.ReadTimeout("timed out"))
        prov = ScraperApiProvider(api_key=self.API_KEY)
        result = await prov.scrape(ScrapeRequest(url=TARGET_URL))
        assert result.success is False
        assert result.failure_reason == FailureReason.TIMEOUT
