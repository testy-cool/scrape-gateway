from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest
import respx

from scrape_gateway.models import FailureReason, ScrapeRequest

GOOD_HTML = (
    "<html><head><title>Test</title></head><body>"
    "<p>This is a realistic page with enough content to pass all minimum length checks.</p>"
    "</body></html>"
)
TARGET_URL = "https://example.com/page"


def provider_cls():
    path = Path(__file__).resolve().parents[1] / "src/sg_browserless/__init__.py"
    spec = importlib.util.spec_from_file_location("sg_browserless_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.BrowserlessProvider


class TestBrowserlessProvider:
    TOKEN = "bl_token_123"
    BASE = "https://browserless.test"

    def test_contract(self):
        provider = provider_cls()
        assert provider.name == "browserless"
        assert provider.cost_rank == 20
        assert provider.capabilities == frozenset({"html", "render_js", "screenshot"})

    def test_cost_estimate_matches_content_and_screenshot_calls(self):
        provider = provider_cls()(base_url=self.BASE, token=self.TOKEN)

        assert provider.estimated_cost_units(ScrapeRequest(TARGET_URL)) == 5
        assert provider.estimated_cost_units(ScrapeRequest(TARGET_URL, screenshot=True)) == 10

    async def test_missing_credentials(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("BROWSERLESS_URL", raising=False)
        monkeypatch.delenv("BROWSERLESS_TOKEN", raising=False)

        provider = provider_cls()
        result = await provider().scrape(ScrapeRequest(url=TARGET_URL))

        assert result.success is False
        assert "Missing" in (result.error or "")
        assert result.failure_reason == FailureReason.PROVIDER_UNAVAILABLE

    @respx.mock
    async def test_content_request_uses_env_and_maps_render_options(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("BROWSERLESS_URL", self.BASE)
        monkeypatch.setenv("BROWSERLESS_TOKEN", self.TOKEN)
        route = respx.post(f"{self.BASE}/content").mock(
            return_value=httpx.Response(200, text=GOOD_HTML)
        )

        provider = provider_cls()
        request = ScrapeRequest(
            url=TARGET_URL,
            wait_event="load",
            wait_selector="#ready",
            extra_wait_ms=500,
            timeout_seconds=20,
        )
        result = await provider().scrape(request)

        assert result.success is True
        assert result.html == GOOD_HTML
        assert result.provider == "browserless"
        assert result.route == "browserless:content"
        assert result.cost_units == 5

        sent = route.calls[0].request
        assert "token" not in sent.url.params
        assert sent.headers["authorization"] == f"Bearer {self.TOKEN}"
        body = json.loads(sent.content)
        assert body == {
            "url": TARGET_URL,
            "gotoOptions": {"waitUntil": "load", "timeout": 20500},
            "waitForSelector": {"selector": "#ready", "timeout": 20500},
        }

    @respx.mock
    async def test_screenshot_response_includes_rendered_html_for_evaluation(self):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        content_route = respx.post(f"{self.BASE}/content").mock(
            return_value=httpx.Response(200, text=GOOD_HTML)
        )
        screenshot_route = respx.post(f"{self.BASE}/screenshot").mock(
            return_value=httpx.Response(
                200,
                content=png_bytes,
                headers={"content-type": "image/png"},
            )
        )

        provider = provider_cls()
        result = await provider(base_url=self.BASE, token=self.TOKEN).scrape(
            ScrapeRequest(url=TARGET_URL, screenshot=True)
        )

        assert result.success is True
        assert result.screenshot == png_bytes
        assert result.html == GOOD_HTML
        assert result.failure_reason is None
        assert result.route == "browserless:content+screenshot"
        assert result.cost_units == 10
        assert content_route.called
        assert screenshot_route.called

    @respx.mock
    async def test_timeout(self):
        respx.post(f"{self.BASE}/content").mock(side_effect=httpx.ReadTimeout("timed out"))

        provider = provider_cls()
        result = await provider(base_url=self.BASE, token=self.TOKEN).scrape(
            ScrapeRequest(url=TARGET_URL)
        )

        assert result.success is False
        assert result.failure_reason == FailureReason.TIMEOUT

    @respx.mock
    async def test_credential_free_proxy_is_passed_as_launch_arg(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("SCRAPE_PROXY_URL", "http://proxy.test:8080")
        route = respx.post(f"{self.BASE}/content").mock(
            return_value=httpx.Response(200, text=GOOD_HTML)
        )

        provider = provider_cls()
        result = await provider(base_url=self.BASE, token=self.TOKEN).scrape(
            ScrapeRequest(url=TARGET_URL)
        )

        launch = json.loads(dict(route.calls.last.request.url.params)["launch"])
        assert launch == {"args": ["--proxy-server=http://proxy.test:8080"]}
        assert result.success is True
        assert result.metadata["proxy"] == "http://proxy.test:8080"

    @respx.mock
    async def test_proxy_credentials_are_reported_not_silently_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Chrome's --proxy-server ignores userinfo, so the proxy cannot be applied.

        The scrape still succeeds unproxied, but the result has to say so — otherwise a
        request the operator believed was proxied leaves from the wrong address with no
        trace of it.
        """
        monkeypatch.setenv("SCRAPE_PROXY_URL", "http://user:pass@proxy.test:8080")
        route = respx.post(f"{self.BASE}/content").mock(
            return_value=httpx.Response(200, text=GOOD_HTML)
        )

        provider = provider_cls()
        result = await provider(base_url=self.BASE, token=self.TOKEN).scrape(
            ScrapeRequest(url=TARGET_URL)
        )

        assert "launch" not in dict(route.calls.last.request.url.params)
        assert result.success is True
        assert "proxy" not in result.metadata
        assert "credentials" in result.metadata["proxy_skipped"]

    @respx.mock
    async def test_dead_proxy_falls_back_to_a_direct_fetch(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SCRAPE_PROXY_URL", "http://proxy.test:8080")
        respx.post(f"{self.BASE}/content").mock(
            side_effect=[
                httpx.Response(500, text="Error: net::ERR_PROXY_CONNECTION_FAILED at /page"),
                httpx.Response(200, text=GOOD_HTML),
            ]
        )

        provider = provider_cls()
        result = await provider(base_url=self.BASE, token=self.TOKEN).scrape(
            ScrapeRequest(url=TARGET_URL)
        )

        assert result.success is True
        assert result.html == GOOD_HTML
        assert result.metadata["proxy_fallback"] == "disabled_after_proxy_error"
        assert "ERR_PROXY_CONNECTION_FAILED" in result.metadata["proxy_error"]
        assert "proxy" not in result.metadata

    @respx.mock
    async def test_no_proxy_configured_adds_no_launch_param(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("SCRAPE_PROXY_URL", raising=False)
        route = respx.post(f"{self.BASE}/content").mock(
            return_value=httpx.Response(200, text=GOOD_HTML)
        )

        provider = provider_cls()
        result = await provider(base_url=self.BASE, token=self.TOKEN).scrape(
            ScrapeRequest(url=TARGET_URL)
        )

        assert dict(route.calls.last.request.url.params) == {}
        assert result.success is True
        assert result.metadata == {}
