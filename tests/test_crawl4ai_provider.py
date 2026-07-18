from __future__ import annotations

import httpx
import respx

from scrape_gateway.models import FailureReason, ScrapeRequest
from scrape_gateway.providers.crawl4ai import Crawl4AIProvider


TARGET = "https://example.com/products"
HTML = "<html><body><h1>Products</h1><p>Rendered product content for testing.</p></body></html>"
MARKDOWN = "# Products\n\nRendered product content for testing and downstream extraction."


async def test_crawl4ai_fails_cleanly_without_service_url(monkeypatch) -> None:
    monkeypatch.delenv("CRAWL4AI_URL", raising=False)

    result = await Crawl4AIProvider().scrape(ScrapeRequest(TARGET))

    assert result.success is False
    assert result.failure_reason is FailureReason.PROVIDER_ERROR
    assert result.error == "Missing CRAWL4AI_URL"


@respx.mock
async def test_crawl4ai_maps_current_docker_api_contract() -> None:
    route = respx.post("http://crawl4ai.test:11235/crawl").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": TARGET,
                        "success": True,
                        "status_code": 200,
                        "html": HTML,
                        "markdown": {"raw_markdown": MARKDOWN},
                    }
                ]
            },
        )
    )

    result = await Crawl4AIProvider(
        base_url="http://crawl4ai.test:11235", token="secret"
    ).scrape(
        ScrapeRequest(
            TARGET,
            render_js=True,
            mobile=True,
            wait_selector="#products",
            extra_wait_ms=750,
            headers={"X-Test": "yes"},
        )
    )

    assert result.success is True
    assert result.html == HTML
    assert result.markdown == MARKDOWN
    assert result.route == "crawl4ai:docker"
    assert route.calls[0].request.headers["authorization"] == "Bearer secret"
    payload = route.calls[0].request.content
    assert b'"urls":["https://example.com/products"]' in payload
    assert b'"type":"BrowserConfig"' in payload
    assert b'"type":"CrawlerRunConfig"' in payload
    assert b'"cache_mode":"bypass"' in payload
    assert b'"wait_for":"css:#products"' in payload
    assert b'"delay_before_return_html":0.75' in payload
    assert b'"X-Test":"yes"' in payload


@respx.mock
async def test_crawl4ai_decodes_requested_screenshot() -> None:
    respx.post("http://crawl4ai.test/crawl").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": TARGET,
                        "success": True,
                        "status_code": 200,
                        "html": HTML,
                        "screenshot": "Y3Jhd2w0YWktcG5n",
                    }
                ]
            },
        )
    )

    result = await Crawl4AIProvider(base_url="http://crawl4ai.test").scrape(
        ScrapeRequest(TARGET, screenshot=True)
    )

    assert result.success is True
    assert result.screenshot == b"crawl4ai-png"


@respx.mock
async def test_crawl4ai_propagates_failed_crawl_result() -> None:
    respx.post("http://crawl4ai.test/crawl").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": TARGET,
                        "success": False,
                        "status_code": 403,
                        "html": "Access denied",
                        "error_message": "Blocked by target",
                    }
                ]
            },
        )
    )

    result = await Crawl4AIProvider(base_url="http://crawl4ai.test").scrape(
        ScrapeRequest(TARGET)
    )

    assert result.success is False
    assert result.status_code == 403
    assert result.failure_reason is FailureReason.HTTP_403
    assert result.error == "Blocked by target"


@respx.mock
async def test_crawl4ai_reports_api_level_auth_failure() -> None:
    respx.post("http://crawl4ai.test/crawl").mock(
        return_value=httpx.Response(401, json={"detail": "Invalid bearer token"})
    )

    result = await Crawl4AIProvider(base_url="http://crawl4ai.test").scrape(
        ScrapeRequest(TARGET)
    )

    assert result.success is False
    assert result.status_code == 401
    assert result.failure_reason is FailureReason.PROVIDER_ERROR
    assert result.error == "Invalid bearer token"
