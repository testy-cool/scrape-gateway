from __future__ import annotations

import base64
from importlib.metadata import version
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from scrape_gateway.cache import ArtifactCache
from scrape_gateway.cli import app as cli_app
from scrape_gateway.memory import DomainMemory
from scrape_gateway.models import ScrapeResult
from scrape_gateway.service import create_app


class FakeGateway:
    def __init__(self, tmp_path) -> None:
        self.cache = ArtifactCache(tmp_path / "cache")
        self.memory = DomainMemory(tmp_path / "memory.sqlite")
        self.providers = [SimpleNamespace(name="mock")]
        self.requests = []

    async def scrape(self, request, *, use_cache=True, use_memory=True):
        self.requests.append((request, use_cache, use_memory))
        return ScrapeResult(
            url=request.url,
            provider="mock",
            success=True,
            status_code=200,
            html="<html><body>Service response with enough useful content.</body></html>",
            markdown="Service response with enough useful content.",
            screenshot=b"png-bytes",
            route="mock:rendered",
            content_validated=True,
            metadata={"run_id": "run-123"},
        )


def test_health_reports_version_and_providers(tmp_path) -> None:
    client = TestClient(create_app(FakeGateway(tmp_path)))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "scrape-gateway",
        "version": version("scrape-gateway"),
        "providers": ["mock"],
    }


def test_scrape_endpoint_maps_request_and_returns_requested_formats(tmp_path) -> None:
    gateway = FakeGateway(tmp_path)
    client = TestClient(create_app(gateway))

    response = client.post(
        "/v1/scrape",
        json={
            "url": "https://shop.example/product",
            "country": "US",
            "render_js": True,
            "premium": True,
            "formats": ["html", "markdown", "screenshot"],
            "use_cache": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["provider"] == "mock"
    assert body["route"] == "mock:rendered"
    assert body["cache_key"] == gateway.cache.key_for_url(
        "https://shop.example/product", render_js=True
    )
    assert body["content"] == {
        "html": "<html><body>Service response with enough useful content.</body></html>",
        "markdown": "Service response with enough useful content.",
        "screenshot": base64.b64encode(b"png-bytes").decode("ascii"),
    }
    request, use_cache, use_memory = gateway.requests[0]
    assert request.url == "https://shop.example/product"
    assert request.country == "US"
    assert request.render_js is True
    assert request.premium is True
    assert request.screenshot is True
    assert use_cache is False
    assert use_memory is True


def test_cache_endpoint_reads_artifacts_by_hash(tmp_path) -> None:
    gateway = FakeGateway(tmp_path)
    cached = ScrapeResult(
        url="https://example.com/page",
        provider="raw_http",
        success=True,
        html="<html><body>Cached API content.</body></html>",
        markdown="Cached API content.",
        screenshot=b"cached-image",
        route="raw_http",
    )
    gateway.cache.save(cached)
    key = gateway.cache.key_for_url(cached.url)
    client = TestClient(create_app(gateway))

    response = client.get(f"/v1/cache/{key}")

    assert response.status_code == 200
    assert response.json() == {
        "key": key,
        "url": cached.url,
        "provider": "raw_http",
        "route": "raw_http",
        "fetched_at": response.json()["fetched_at"],
        "html": cached.html,
        "markdown": cached.markdown,
        "screenshot": base64.b64encode(b"cached-image").decode("ascii"),
    }
    assert client.get("/v1/cache/not-a-hash").status_code == 404


def test_stats_endpoint_returns_domain_memory(tmp_path) -> None:
    gateway = FakeGateway(tmp_path)
    gateway.memory.remember_success(
        "https://shop.example/product", "mock", "US", True, True, tier="rendered"
    )
    client = TestClient(create_app(gateway))

    response = client.get("/v1/stats/shop.example")

    assert response.status_code == 200
    assert response.json()["domain"] == "shop.example"
    assert response.json()["providers"][0]["provider"] == "mock"
    assert response.json()["providers"][0]["success_count"] == 1


def test_bearer_token_protects_v1_routes_but_not_health(tmp_path) -> None:
    client = TestClient(create_app(FakeGateway(tmp_path), token="secret"))

    assert client.get("/health").status_code == 200
    assert client.get("/v1/stats/example.com").status_code == 401
    response = client.get("/v1/stats/example.com", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200


def test_serve_command_starts_uvicorn_with_requested_bind_address() -> None:
    service_app = object()
    with (
        patch("scrape_gateway.service.create_app", return_value=service_app),
        patch("uvicorn.run") as run,
    ):
        result = CliRunner().invoke(
            cli_app,
            ["serve", "--host", "127.0.0.1", "--port", "8123", "--token", "local-token"],
        )

    assert result.exit_code == 0
    run.assert_called_once_with(service_app, host="127.0.0.1", port=8123)


def test_mcp_service_exposes_rest_routes_alongside_console_and_mcp() -> None:
    from scrape_gateway.mcp_server import app

    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/health" in paths
    assert "/v1" in paths
    assert "/" in paths
