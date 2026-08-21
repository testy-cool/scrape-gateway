from __future__ import annotations

import base64

import httpx
import pytest
import respx

from scrape_gateway.models import FailureReason, ScrapeRequest
from scrape_gateway.providers.brightdata import BrightDataProvider
from scrape_gateway.providers.firecrawl import FirecrawlProvider
from scrape_gateway.providers.jina_reader import JinaReaderProvider
from scrape_gateway.providers.oxylabs import OxylabsProvider
from scrape_gateway.providers.scrapfly import ScrapflyProvider
from scrape_gateway.providers.scrapingant import ScrapingAntProvider
from scrape_gateway.providers.spider_cloud import SpiderCloudProvider
from scrape_gateway.providers.zenrows import ZenRowsProvider

TARGET = "https://example.com/products"
HTML = (
    "<html><body><h1>Products</h1><p>Complete provider response with enough "
    "meaningful text for gateway validation.</p></body></html>"
)
MARKDOWN = (
    "# Products\n\nComplete provider response with enough meaningful text for deterministic "
    "content validation and downstream extraction."
)


@pytest.mark.parametrize(
    ("provider", "scrape_request", "expected"),
    [
        (SpiderCloudProvider(api_key="key"), ScrapeRequest(TARGET), 1),
        (
            SpiderCloudProvider(api_key="key"),
            ScrapeRequest(TARGET, render_js=True),
            2,
        ),
        (FirecrawlProvider(api_key="key"), ScrapeRequest(TARGET), 1),
        (
            FirecrawlProvider(api_key="key"),
            ScrapeRequest(TARGET, premium=True),
            5,
        ),
        (ZenRowsProvider(api_key="key"), ScrapeRequest(TARGET), 1),
        (
            ZenRowsProvider(api_key="key"),
            ScrapeRequest(TARGET, render_js=True),
            5,
        ),
        (
            ZenRowsProvider(api_key="key"),
            ScrapeRequest(TARGET, country="US"),
            10,
        ),
        (
            ZenRowsProvider(api_key="key"),
            ScrapeRequest(TARGET, country="US", render_js=True),
            25,
        ),
        (
            OxylabsProvider(username="user", password="pass"),
            ScrapeRequest(TARGET),
            1,
        ),
        (
            OxylabsProvider(username="user", password="pass"),
            ScrapeRequest(TARGET, screenshot=True),
            10,
        ),
        (
            BrightDataProvider(api_key="key", zone="zone"),
            ScrapeRequest(TARGET),
            5,
        ),
        (
            BrightDataProvider(api_key="key", zone="zone"),
            ScrapeRequest(TARGET, screenshot=True),
            10,
        ),
        (ScrapflyProvider(api_key="key"), ScrapeRequest(TARGET), 1),
        (ScrapingAntProvider(api_key="key"), ScrapeRequest(TARGET), 1),
        (
            ScrapingAntProvider(api_key="key"),
            ScrapeRequest(TARGET, render_js=True),
            10,
        ),
        (
            ScrapingAntProvider(api_key="key"),
            ScrapeRequest(TARGET, premium=True),
            25,
        ),
        (
            ScrapingAntProvider(api_key="key"),
            ScrapeRequest(TARGET, render_js=True, premium=True),
            125,
        ),
        (
            ScrapflyProvider(api_key="key", cost_budget=25),
            ScrapeRequest(
                TARGET,
                premium=True,
                metadata={"_remaining_cost_units": 10.5},
            ),
            10,
        ),
    ],
)
def test_additional_provider_cost_estimates_match_billed_request_shape(
    provider, scrape_request, expected
) -> None:
    assert provider.estimated_cost_units(scrape_request) == expected


@pytest.mark.parametrize(
    "provider,environment",
    [
        (ScrapflyProvider, ["SCRAPFLY_API_KEY"]),
        (ScrapingAntProvider, ["SCRAPINGANT_API_KEY"]),
        (FirecrawlProvider, ["FIRECRAWL_API_KEY"]),
        (ZenRowsProvider, ["ZENROWS_API_KEY"]),
        (OxylabsProvider, ["OXYLABS_USERNAME", "OXYLABS_PASSWORD"]),
        (BrightDataProvider, ["BRIGHTDATA_API_KEY", "BRIGHTDATA_WEB_UNLOCKER_ZONE"]),
        (SpiderCloudProvider, ["SPIDER_CLOUD_API_KEY"]),
    ],
)
async def test_paid_api_providers_fail_cleanly_without_credentials(
    provider, environment, monkeypatch
) -> None:
    for name in environment:
        monkeypatch.delenv(name, raising=False)

    result = await provider().scrape(ScrapeRequest(TARGET))

    assert result.success is False
    assert "Missing" in (result.error or "")
    assert result.failure_reason is FailureReason.PROVIDER_UNAVAILABLE


@respx.mock
async def test_scrapingant_maps_request_and_records_reported_cost() -> None:
    route = respx.get("https://api.scrapingant.com/v2/general").mock(
        return_value=httpx.Response(
            200,
            text=HTML,
            headers={"ant-page-status-code": "200", "ant-credits-cost": "125"},
        )
    )

    result = await ScrapingAntProvider(api_key="ant-key").scrape(
        ScrapeRequest(
            TARGET,
            country="de",
            render_js=True,
            premium=True,
            wait_selector="#products",
            timeout_seconds=70,
            referer="https://search.example/",
            headers={"X-Customer": "yes", "User-Agent": "router-generated"},
        )
    )

    assert result.success is True
    assert result.html == HTML
    assert result.status_code == 200
    assert result.route == "scrapingant:residential"
    assert result.cost_units == 125
    assert result.attempt_ledger[0].cost_provenance == "exact"
    params = route.calls[0].request.url.params
    assert params["x-api-key"] == "ant-key"
    assert params["url"] == TARGET
    assert params["browser"] == "true"
    assert params["proxy_type"] == "residential"
    assert params["proxy_country"] == "DE"
    assert params["wait_for_selector"] == "#products"
    assert params["timeout"] == "60"
    assert route.calls[0].request.headers["ant-x-customer"] == "yes"
    assert route.calls[0].request.headers["ant-referer"] == "https://search.example/"
    assert "ant-user-agent" not in route.calls[0].request.headers


@respx.mock
async def test_scrapingant_uses_target_status_for_validation() -> None:
    respx.get("https://api.scrapingant.com/v2/general").mock(
        return_value=httpx.Response(
            200,
            text="<html><body>Access denied by the target website with enough detail.</body></html>",
            headers={"ant-page-status-code": "403", "ant-credits-cost": "10"},
        )
    )

    result = await ScrapingAntProvider(api_key="ant-key").scrape(
        ScrapeRequest(TARGET, render_js=True)
    )

    assert result.success is False
    assert result.status_code == 403
    assert result.failure_reason is FailureReason.HTTP_403
    assert result.metadata["provider_status_code"] == 200


@respx.mock
async def test_scrapingant_maps_api_rate_limit_to_http_429() -> None:
    respx.get("https://api.scrapingant.com/v2/general").mock(
        return_value=httpx.Response(409, text="Concurrent request limit reached")
    )

    result = await ScrapingAntProvider(api_key="ant-key").scrape(ScrapeRequest(TARGET))

    assert result.success is False
    assert result.status_code == 409
    assert result.failure_reason is FailureReason.HTTP_429
    assert result.attempt_ledger[0].cost_provenance == "estimated"


@respx.mock
async def test_scrapfly_maps_browser_country_asp_and_cost_budget() -> None:
    route = respx.get("https://api.scrapfly.io/scrape").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {"content": HTML, "status_code": 200},
                "context": {"cost": 7},
            },
            headers={"x-scrapfly-api-cost": "19"},
        )
    )

    result = await ScrapflyProvider(api_key="scrapfly-key", cost_budget=25).scrape(
        ScrapeRequest(
            TARGET,
            country="US",
            render_js=True,
            premium=True,
            wait_selector="#products",
            extra_wait_ms=1500,
        )
    )

    assert result.success is True
    assert result.html == HTML
    assert result.cost_units == 19
    assert result.route == "scrapfly:asp"
    assert result.attempt_ledger[0].cost_provenance == "exact"
    assert result.attempt_ledger[0].cost_units == 19
    params = route.calls[0].request.url.params
    assert params["key"] == "scrapfly-key"
    assert params["url"] == TARGET
    assert params["country"] == "us"
    assert params["render_js"] == "true"
    assert params["asp"] == "true"
    assert params["cost_budget"] == "25"
    assert params["wait_for_selector"] == "#products"
    assert params["rendering_wait"] == "1500"


@respx.mock
async def test_scrapfly_caps_internal_cost_budget_to_gateway_remainder() -> None:
    route = respx.get("https://api.scrapfly.io/scrape").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {"content": HTML, "status_code": 200},
                "context": {"cost": 7},
            },
        )
    )

    result = await ScrapflyProvider(api_key="scrapfly-key", cost_budget=25).scrape(
        ScrapeRequest(
            TARGET,
            premium=True,
            metadata={"_remaining_cost_units": 10.5},
        )
    )

    assert result.success is True
    assert route.calls[0].request.url.params["cost_budget"] == "10"


@respx.mock
async def test_scrapfly_marks_context_cost_exact_without_header() -> None:
    respx.get("https://api.scrapfly.io/scrape").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {"content": HTML, "status_code": 200},
                "context": {"cost": 7},
            },
        )
    )

    result = await ScrapflyProvider(api_key="scrapfly-key").scrape(ScrapeRequest(TARGET))

    assert result.cost_units == 7
    assert result.attempt_ledger[0].cost_provenance == "exact"


@respx.mock
async def test_scrapfly_marks_hardcoded_cost_fallback_estimated() -> None:
    respx.get("https://api.scrapfly.io/scrape").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {"content": HTML, "status_code": 200},
                "context": {},
            },
        )
    )

    result = await ScrapflyProvider(api_key="scrapfly-key").scrape(ScrapeRequest(TARGET))

    assert result.cost_units == 1
    assert result.attempt_ledger[0].cost_provenance == "estimated"


@respx.mock
async def test_scrapfly_preserves_exact_context_cost_on_failed_response() -> None:
    respx.get("https://api.scrapfly.io/scrape").mock(
        return_value=httpx.Response(
            500,
            json={
                "result": {"content": "Upstream failure", "status_code": 500},
                "context": {"cost": 7},
            },
        )
    )

    result = await ScrapflyProvider(api_key="scrapfly-key").scrape(ScrapeRequest(TARGET))

    assert result.success is False
    assert result.failure_reason == FailureReason.HTTP_5XX
    assert result.cost_units == 7
    assert result.attempt_ledger[0].cost_provenance == "exact"


@respx.mock
async def test_scrapfly_handles_non_json_failed_response_with_estimated_cost() -> None:
    respx.get("https://api.scrapfly.io/scrape").mock(
        return_value=httpx.Response(500, text="<html>upstream failure</html>")
    )

    result = await ScrapflyProvider(api_key="scrapfly-key").scrape(ScrapeRequest(TARGET))

    assert result.success is False
    assert result.failure_reason == FailureReason.HTTP_5XX
    assert result.cost_units == 1
    assert result.attempt_ledger[0].cost_provenance == "estimated"


@respx.mock
async def test_scrapfly_handles_malformed_failed_response_json_shape() -> None:
    respx.get("https://api.scrapfly.io/scrape").mock(
        return_value=httpx.Response(
            500,
            json={"result": "upstream failure", "context": "unavailable"},
        )
    )

    result = await ScrapflyProvider(api_key="scrapfly-key").scrape(ScrapeRequest(TARGET))

    assert result.success is False
    assert result.failure_reason == FailureReason.HTTP_5XX
    assert result.cost_units == 1
    assert result.attempt_ledger[0].cost_provenance == "estimated"


@respx.mock
async def test_scrapfly_classifies_failed_api_envelope_from_outer_status() -> None:
    respx.get("https://api.scrapfly.io/scrape").mock(
        return_value=httpx.Response(
            407,
            json={
                "result": {"content": "", "status_code": 200},
                "context": {"cost": 7},
            },
        )
    )

    result = await ScrapflyProvider(api_key="scrapfly-key").scrape(ScrapeRequest(TARGET))

    assert result.success is False
    assert result.status_code == 407
    assert result.failure_reason == FailureReason.PROXY_ERROR
    assert result.cost_units == 7
    assert result.attempt_ledger[0].cost_provenance == "exact"


@pytest.mark.parametrize("invalid_header", ["not-a-number", "nan", "inf", "-1"])
@respx.mock
async def test_scrapfly_falls_back_from_invalid_header_to_exact_context_cost(
    invalid_header: str,
) -> None:
    respx.get("https://api.scrapfly.io/scrape").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {"content": HTML, "status_code": 200},
                "context": {"cost": 7},
            },
            headers={"x-scrapfly-api-cost": invalid_header},
        )
    )

    result = await ScrapflyProvider(api_key="scrapfly-key").scrape(ScrapeRequest(TARGET))

    assert result.success is True
    assert result.cost_units == 7
    assert result.attempt_ledger[0].cost_provenance == "exact"


@respx.mock
async def test_scrapfly_falls_back_to_outer_status_when_inner_status_is_malformed() -> None:
    respx.get("https://api.scrapfly.io/scrape").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {"content": HTML, "status_code": "not-a-status"},
                "context": {"cost": 3},
            },
        )
    )

    result = await ScrapflyProvider(api_key="scrapfly-key").scrape(ScrapeRequest(TARGET))

    assert result.success is True
    assert result.status_code == 200
    assert result.cost_units == 3
    assert result.attempt_ledger[0].cost_provenance == "exact"


@respx.mock
async def test_scrapfly_failed_clob_fetch_preserves_reported_spend() -> None:
    clob_url = "https://storage.scrapfly.test/result"
    respx.get("https://api.scrapfly.io/scrape").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {
                    "content": clob_url,
                    "format": "clob",
                    "status_code": 200,
                },
                "context": {"cost": 7},
            },
        )
    )
    respx.get(clob_url).mock(return_value=httpx.Response(500, text="storage unavailable"))

    result = await ScrapflyProvider(api_key="scrapfly-key").scrape(ScrapeRequest(TARGET))

    assert result.success is False
    assert result.status_code == 500
    assert result.failure_reason == FailureReason.HTTP_5XX
    assert result.cost_units == 7
    assert result.attempt_ledger[0].cost_provenance == "exact"
    assert result.attempt_ledger[0].success is False
    assert result.attempt_ledger[0].latency_ms is not None


@respx.mock
async def test_firecrawl_returns_html_markdown_and_downloaded_screenshot() -> None:
    screenshot_url = "https://cdn.firecrawl.test/screenshot.png"
    route = respx.post("https://api.firecrawl.dev/v2/scrape").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "html": HTML,
                    "markdown": MARKDOWN,
                    "screenshot": screenshot_url,
                    "metadata": {"statusCode": 200},
                },
            },
        )
    )
    respx.get(screenshot_url).mock(
        return_value=httpx.Response(
            200, content=b"firecrawl-png", headers={"content-type": "image/png"}
        )
    )

    result = await FirecrawlProvider(api_key="firecrawl-key").scrape(
        ScrapeRequest(
            TARGET,
            country="US",
            render_js=True,
            screenshot=True,
            mobile=True,
            extra_wait_ms=500,
            block_ads=True,
        )
    )

    assert result.success is True
    assert result.html == HTML
    assert result.markdown == MARKDOWN
    assert result.screenshot == b"firecrawl-png"
    payload = route.calls[0].request.read()
    assert b'"url":"https://example.com/products"' in payload
    assert b'"type":"screenshot"' in payload
    assert b'"country":"US"' in payload
    assert b'"mobile":true' in payload
    assert route.calls[0].request.headers["authorization"] == "Bearer firecrawl-key"


@respx.mock
async def test_jina_reader_supports_an_optional_key_and_browser_engine() -> None:
    route = respx.get(f"https://r.jina.ai/{TARGET}").mock(
        return_value=httpx.Response(200, text=MARKDOWN)
    )

    result = await JinaReaderProvider(api_key="jina-key").scrape(
        ScrapeRequest(TARGET, render_js=True, wait_selector="main")
    )

    assert result.success is True
    assert result.html == MARKDOWN
    assert result.markdown == MARKDOWN
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer jina-key"
    assert request.headers["x-respond-with"] == "markdown"
    assert request.headers["x-engine"] == "browser"
    assert request.headers["x-wait-for-selector"] == "main"


@respx.mock
async def test_zenrows_maps_manual_unblocking_options() -> None:
    route = respx.get("https://api.zenrows.com/v1/").mock(
        return_value=httpx.Response(200, text=HTML)
    )

    result = await ZenRowsProvider(api_key="zen-key").scrape(
        ScrapeRequest(TARGET, country="DE", render_js=True, premium=True)
    )

    assert result.success is True
    assert result.html == HTML
    params = route.calls[0].request.url.params
    assert params["apikey"] == "zen-key"
    assert params["url"] == TARGET
    assert params["js_render"] == "true"
    assert params["premium_proxy"] == "true"
    assert params["proxy_country"] == "de"
    assert params["original_status"] == "true"


@respx.mock
async def test_oxylabs_uses_realtime_universal_source_and_basic_auth() -> None:
    route = respx.post("https://realtime.oxylabs.io/v1/queries").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "content": HTML,
                        "status_code": 200,
                        "job_id": "job-123",
                    }
                ]
            },
        )
    )

    result = await OxylabsProvider(username="oxy-user", password="oxy-pass").scrape(
        ScrapeRequest(TARGET, country="United States", render_js=True, mobile=True)
    )

    assert result.success is True
    assert result.html == HTML
    assert result.metadata["job_id"] == "job-123"
    request = route.calls[0].request
    assert (
        request.headers["authorization"]
        == "Basic " + base64.b64encode(b"oxy-user:oxy-pass").decode()
    )
    payload = request.content
    assert b'"source":"universal"' in payload
    assert b'"render":"html"' in payload
    assert b'"geo_location":"United States"' in payload
    assert b'"user_agent_type":"mobile"' in payload


@respx.mock
async def test_oxylabs_decodes_png_screenshot_results() -> None:
    encoded = base64.b64encode(b"oxylabs-png").decode()
    respx.post("https://realtime.oxylabs.io/v1/queries").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"content": encoded, "status_code": 200}]},
        )
    )

    result = await OxylabsProvider(username="user", password="pass").scrape(
        ScrapeRequest(TARGET, screenshot=True)
    )

    assert result.success is True
    assert result.html is None
    assert result.screenshot == b"oxylabs-png"


@respx.mock
async def test_brightdata_web_unlocker_uses_zone_and_bearer_key() -> None:
    route = respx.post("https://api.brightdata.com/request").mock(
        return_value=httpx.Response(200, text=HTML)
    )

    result = await BrightDataProvider(api_key="bright-key", zone="unlocker-zone").scrape(
        ScrapeRequest(TARGET, render_js=True, premium=True)
    )

    assert result.success is True
    assert result.html == HTML
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer bright-key"
    assert b'"zone":"unlocker-zone"' in request.content
    assert b'"url":"https://example.com/products"' in request.content
    assert b'"format":"raw"' in request.content


@respx.mock
async def test_brightdata_returns_png_for_screenshot_requests() -> None:
    route = respx.post("https://api.brightdata.com/request").mock(
        return_value=httpx.Response(
            200, content=b"bright-png", headers={"content-type": "image/png"}
        )
    )

    result = await BrightDataProvider(api_key="key", zone="zone").scrape(
        ScrapeRequest(TARGET, screenshot=True)
    )

    assert result.success is True
    assert result.screenshot == b"bright-png"
    assert b'"data_format":"screenshot"' in route.calls[0].request.content


@respx.mock
async def test_spider_cloud_maps_smart_mode_and_markdown_response() -> None:
    route = respx.post("https://api.spider.cloud/scrape").mock(
        return_value=httpx.Response(
            200,
            json=[{"url": TARGET, "content": MARKDOWN, "status": 200}],
        )
    )

    result = await SpiderCloudProvider(api_key="spider-key").scrape(
        ScrapeRequest(TARGET, render_js=True, output_format="markdown", wait_selector="article")
    )

    assert result.success is True
    assert result.html == MARKDOWN
    assert result.markdown == MARKDOWN
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer spider-key"
    assert b'"request":"chrome"' in request.content
    assert b'"return_format":"markdown"' in request.content
    assert b'"selector":"article"' in request.content
