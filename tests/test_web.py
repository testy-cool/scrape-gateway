from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import httpx

from scrape_gateway.config import GatewayConfig, TelemetryConfig
from scrape_gateway.models import ScrapeResult


class FakeGateway:
    def __init__(self, result: ScrapeResult) -> None:
        self.result = result
        self.requests = []
        self.calls = []
        self.providers = []

    async def scrape(self, request, *, use_cache: bool, use_memory: bool):
        self.requests.append(request)
        self.calls.append({"use_cache": use_cache, "use_memory": use_memory})
        return self.result


def _config(root: Path, *, evaluation_mode: str = "audit") -> GatewayConfig:
    config = GatewayConfig(telemetry=TelemetryConfig(root=str(root)))
    config.evaluation = replace(config.evaluation, mode=evaluation_mode)
    return config


def _client(app, *, token: str | None = None) -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token else None
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers=headers,
    )


async def test_console_api_requires_the_configured_bearer_token(tmp_path: Path) -> None:
    from scrape_gateway.web import create_console_app

    app = create_console_app(
        token="operator-secret",
        get_gateway=lambda: FakeGateway(ScrapeResult("https://example.com", "fake", True)),
        get_config=lambda: _config(tmp_path),
    )

    async with _client(app) as client:
        response = await client.get("/api/runs")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"

    async with _client(app, token="wrong") as client:
        response = await client.get("/api/runs")
    assert response.status_code == 401

    async with _client(app, token="operator-secret") as client:
        response = await client.get("/api/runs")
    assert response.status_code == 200


async def test_scrape_api_forwards_operator_options_and_returns_a_safe_preview(
    tmp_path: Path,
) -> None:
    from scrape_gateway.web import create_console_app

    gateway = FakeGateway(
        ScrapeResult(
            url="https://example.com/products",
            provider="browserless",
            success=True,
            status_code=200,
            route="browserless:content+screenshot",
            html="<main><script>alert('no')</script><h1>Products</h1></main>",
            markdown="# Products\n\nWidget — $19.99",
            screenshot=b"\x89PNG\r\n\x1a\nimage",
            metadata={
                "run_id": "run123",
                "telemetry_report": str(tmp_path / "run123" / "report.json"),
                "evaluation": {
                    "status": "completed",
                    "verdict": "pass",
                    "needs_human_review": False,
                    "recommended_action": "accept",
                },
            },
        )
    )
    app = create_console_app(
        get_gateway=lambda: gateway,
        get_config=lambda: _config(tmp_path),
    )

    async with _client(app) as client:
        response = await client.post(
            "/api/scrapes",
            json={
                "url": "example.com/products",
                "country": "us",
                "render_js": True,
                "premium": True,
                "screenshot": True,
                "mobile": True,
                "block_ads": True,
                "output_format": "markdown",
                "evaluation_goal": "Capture product names and prices",
                "use_cache": False,
            },
        )

    assert response.status_code == 200
    request = gateway.requests[0]
    assert request.url == "example.com/products"
    assert request.country == "us"
    assert request.render_js is True
    assert request.premium is True
    assert request.screenshot is True
    assert request.mobile is True
    assert request.block_ads is True
    assert request.output_format == "markdown"
    assert request.metadata["evaluation_goal"] == "Capture product names and prices"
    assert gateway.calls == [{"use_cache": False, "use_memory": False}]

    payload = response.json()
    assert payload["run_id"] == "run123"
    assert payload["evaluation"]["verdict"] == "pass"
    assert payload["preview"]["markdown"].startswith("# Products")
    assert "<script>" in payload["preview"]["html"]
    assert payload["preview"]["has_screenshot"] is True
    assert "screenshot" not in payload["preview"]


async def test_run_api_lists_summaries_and_serves_contained_artifacts(tmp_path: Path) -> None:
    from scrape_gateway.web import create_console_app

    run_id = "abc123def456"
    run_dir = tmp_path / run_id
    evaluation_dir = run_dir / "evaluation"
    evaluation_dir.mkdir(parents=True)
    report = {
        "run_id": run_id,
        "started_at": "2026-07-16T12:00:00+00:00",
        "elapsed_ms": 875,
        "url": "https://example.com/products",
        "domain": "example.com",
        "success": True,
        "useful": True,
        "diagnosis": "success",
        "final": {
            "provider": "browserless",
            "route": "browserless:content+screenshot",
            "status": 200,
            "chars": 1200,
            "markdown_chars": 480,
        },
        "evaluation": {
            "status": "completed",
            "verdict": "fail",
            "needs_human_review": True,
            "page_type": "product listing",
            "root_cause": "missing prices",
            "recommended_action": "retry_rendered",
            "checks": {"goal_coverage": {"result": "fail", "evidence": "Prices are absent."}},
            "improvement_opportunities": ["Wait for the product grid."],
            "usage": {"total_tokens": 900, "cost": 0.0003},
        },
    }
    (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (run_dir / "attempts.jsonl").write_text('{"provider":"browserless"}\n', encoding="utf-8")
    (evaluation_dir / "final.md").write_text("# Products", encoding="utf-8")
    (evaluation_dir / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\nimage")

    app = create_console_app(
        get_gateway=lambda: FakeGateway(ScrapeResult("https://example.com", "fake", True)),
        get_config=lambda: _config(tmp_path),
    )

    async with _client(app) as client:
        runs_response = await client.get("/api/runs?limit=10")
        detail_response = await client.get(f"/api/runs/{run_id}")
        summary_response = await client.get("/api/evaluations?limit=10")
        markdown_response = await client.get(f"/api/runs/{run_id}/artifacts/evaluation/final.md")
        image_response = await client.get(f"/api/runs/{run_id}/artifacts/evaluation/screenshot.png")

    runs = runs_response.json()["runs"]
    assert runs[0]["run_id"] == run_id
    assert runs[0]["provider"] == "browserless"
    assert runs[0]["evaluation"]["verdict"] == "fail"

    detail = detail_response.json()
    assert detail["report"]["evaluation"]["checks"]["goal_coverage"]["result"] == "fail"
    assert {item["path"] for item in detail["artifacts"]} == {
        "attempts.jsonl",
        "evaluation/final.md",
        "evaluation/screenshot.png",
        "report.json",
    }
    assert summary_response.json()["summary"]["review_queue"][0]["run_id"] == run_id
    assert markdown_response.text == "# Products"
    assert markdown_response.headers["content-type"].startswith("text/plain")
    assert image_response.content.startswith(b"\x89PNG")
    assert image_response.headers["content-type"] == "image/png"


async def test_artifact_api_rejects_paths_outside_the_run_directory(tmp_path: Path) -> None:
    from scrape_gateway.web import create_console_app

    run_dir = tmp_path / "safe123"
    run_dir.mkdir()
    (run_dir / "report.json").write_text('{"run_id":"safe123"}', encoding="utf-8")
    (tmp_path / "secret.txt").write_text("do not expose", encoding="utf-8")
    app = create_console_app(
        get_gateway=lambda: FakeGateway(ScrapeResult("https://example.com", "fake", True)),
        get_config=lambda: _config(tmp_path),
    )

    async with _client(app) as client:
        response = await client.get("/api/runs/safe123/artifacts/%2E%2E/secret.txt")

    assert response.status_code == 404
    assert "do not expose" not in response.text


async def test_console_serves_packaged_assets_without_authentication(tmp_path: Path) -> None:
    from scrape_gateway.web import create_console_app

    app = create_console_app(
        token="operator-secret",
        get_gateway=lambda: FakeGateway(ScrapeResult("https://example.com", "fake", True)),
        get_config=lambda: _config(tmp_path),
    )

    async with _client(app) as client:
        page = await client.get("/")
        css = await client.get("/assets/app.css")
        script = await client.get("/assets/app.js")

    assert page.status_code == 200
    assert "Scrape Gateway" in page.text
    assert css.status_code == 200
    assert script.status_code == 200


async def test_console_shell_exposes_the_full_operator_workflow(tmp_path: Path) -> None:
    from scrape_gateway.web import create_console_app

    app = create_console_app(
        token="operator-secret",
        get_gateway=lambda: FakeGateway(ScrapeResult("https://example.com", "fake", True)),
        get_config=lambda: _config(tmp_path),
    )

    async with _client(app) as client:
        page = await client.get("/")
        css = await client.get("/assets/app.css")
        script = await client.get("/assets/app.js")

    for element_id in (
        "auth-dialog",
        "scrape-form",
        "url-input",
        "evaluation-goal",
        "audit-summary",
        "run-list",
        "run-inspector",
        "artifact-viewer",
    ):
        assert f'id="{element_id}"' in page.text
    assert "sessionStorage" in script.text
    assert 'fetchJson("/api/runs' in script.text
    assert 'fetchJson("/api/evaluations' in script.text
    assert "textContent" in script.text
    assert "@media (max-width: 760px)" in css.text
    assert "prefers-reduced-motion" in css.text


def test_service_app_keeps_the_console_route() -> None:
    from scrape_gateway.mcp_server import app

    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/" in paths


def test_service_app_preserves_authenticated_mcp_middleware(monkeypatch) -> None:
    import importlib

    from starlette.testclient import TestClient

    import scrape_gateway.mcp_server as mcp_server

    with monkeypatch.context() as env:
        env.setenv("SGW_MCP_TOKEN", "operator-secret")
        env.setenv("SGW_MCP_PORT", "8999")
        env.setenv("SGW_MCP_URL", "http://localhost:8999")
        mcp_server = importlib.reload(mcp_server)

        with TestClient(mcp_server.app, base_url="http://localhost:8999") as client:
            response = client.post(
                "/mcp",
                headers={
                    "Authorization": "Bearer operator-secret",
                    "Accept": "application/json, text/event-stream",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )

        assert response.status_code == 200
        payload = response.json()
        assert "result" in payload
        assert "scrape" in {tool["name"] for tool in payload["result"]["tools"]}

    importlib.reload(mcp_server)
