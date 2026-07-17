from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

import httpx

from scrape_gateway.config import GatewayConfig, ProviderConfig, TelemetryConfig
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


class BlockingGateway:
    def __init__(self) -> None:
        self.providers = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def scrape(self, request, *, use_cache: bool, use_memory: bool):
        self.started.set()
        await self.release.wait()
        return ScrapeResult(
            request.url,
            "browserless",
            True,
            status_code=200,
            metadata={"run_id": "finished123"},
        )


class ProgressGateway(BlockingGateway):
    async def scrape(self, request, *, use_cache: bool, use_memory: bool):
        from scrape_gateway.progress import emit_progress

        emit_progress(
            id="provider-1",
            name="Browserless request",
            kind="provider",
            status="running",
            outcome="requesting",
            summary="Waiting for Browserless",
            attributes={"provider": "browserless", "screenshot_requested": request.screenshot},
        )
        return await super().scrape(request, use_cache=use_cache, use_memory=use_memory)


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


async def test_active_scrape_remains_visible_after_the_console_request_is_cancelled(
    tmp_path: Path,
) -> None:
    from scrape_gateway.web import create_console_app

    gateway = BlockingGateway()
    app = create_console_app(
        get_gateway=lambda: gateway,
        get_config=lambda: _config(tmp_path),
    )

    async with _client(app) as client:
        request_task = asyncio.create_task(
            client.post(
                "/api/scrapes",
                json={
                    "url": "https://example.com/slow",
                    "screenshot": True,
                    "use_cache": False,
                },
            )
        )
        await asyncio.wait_for(gateway.started.wait(), timeout=1)

        active = (await client.get("/api/runs")).json()["active_runs"]
        assert len(active) == 1
        assert active[0]["url"] == "https://example.com/slow"
        assert active[0]["pending"] is True
        assert active[0]["payload"]["screenshot"] is True

        request_task.cancel()
        with suppress(asyncio.CancelledError):
            await request_task

        restored = (await client.get("/api/runs")).json()["active_runs"]
        assert restored[0]["run_id"] == active[0]["run_id"]

        gateway.release.set()
        for _ in range(20):
            if not (await client.get("/api/runs")).json()["active_runs"]:
                break
            await asyncio.sleep(0.01)

        assert (await client.get("/api/runs")).json()["active_runs"] == []


async def test_active_scrape_exposes_incremental_trace_steps(tmp_path: Path) -> None:
    from scrape_gateway.web import create_console_app

    gateway = ProgressGateway()
    app = create_console_app(
        get_gateway=lambda: gateway,
        get_config=lambda: _config(tmp_path),
    )

    async with _client(app) as client:
        request_task = asyncio.create_task(
            client.post(
                "/api/scrapes",
                json={"url": "https://example.com/slow", "screenshot": True},
            )
        )
        await asyncio.wait_for(gateway.started.wait(), timeout=1)

        active = (await client.get("/api/runs")).json()["active_runs"][0]
        assert [step["id"] for step in active["steps"]] == ["request", "routing", "provider-1"]
        assert active["steps"][2]["status"] == "running"
        assert active["steps"][2]["attributes"]["screenshot_requested"] is True
        assert active["provider"] == "browserless"
        assert active["activity"] == "Waiting for Browserless"
        assert active["current_step"] == {
            "id": "provider-1",
            "name": "Browserless request",
            "kind": "provider",
            "status": "running",
            "outcome": "requesting",
            "offset_ms": active["steps"][2]["offset_ms"],
        }

        gateway.release.set()
        await request_task


async def test_settings_api_updates_provider_availability_and_timeouts(tmp_path: Path) -> None:
    from scrape_gateway.web import create_console_app

    current = _config(tmp_path)
    current.providers = [
        ProviderConfig(name="raw_http", enabled=True, timeout_seconds=10),
        ProviderConfig(name="scrapedrive", enabled=True),
    ]
    applied = []

    def apply_settings(settings):
        applied.append(settings)
        current.request.default_timeout_seconds = settings["default_timeout_seconds"]
        current.evaluation.timeout_seconds = settings["evaluation_timeout_seconds"]
        for provider in current.providers:
            update = next(item for item in settings["providers"] if item["name"] == provider.name)
            provider.enabled = update["enabled"]
            provider.timeout_seconds = update["timeout_seconds"]
        return current

    app = create_console_app(
        get_gateway=lambda: FakeGateway(ScrapeResult("https://example.com", "fake", True)),
        get_config=lambda: current,
        apply_settings=apply_settings,
    )

    async with _client(app) as client:
        initial = (await client.get("/api/settings")).json()
        response = await client.put(
            "/api/settings",
            json={
                "default_timeout_seconds": 28,
                "evaluation_timeout_seconds": 80,
                "providers": [
                    {"name": "raw_http", "enabled": True, "timeout_seconds": 9},
                    {"name": "scrapedrive", "enabled": False, "timeout_seconds": None},
                ],
            },
        )

    assert initial["default_timeout_seconds"] == 45
    assert response.status_code == 200
    assert applied[0]["providers"][1]["enabled"] is False
    assert response.json()["providers_by_name"]["scrapedrive"]["enabled"] is False
    assert response.json()["default_timeout_seconds"] == 28


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


async def test_run_detail_exposes_an_ordered_trace_without_inventing_step_timings(
    tmp_path: Path,
) -> None:
    from scrape_gateway.web import create_console_app

    run_id = "trace123"
    run_dir = tmp_path / run_id
    evaluation_dir = run_dir / "evaluation"
    evaluation_dir.mkdir(parents=True)
    report = {
        "run_id": run_id,
        "started_at": "2026-07-16T12:00:00+00:00",
        "finished_at": "2026-07-16T12:00:01.500000+00:00",
        "elapsed_ms": 1500,
        "url": "https://example.com/products",
        "success": True,
        "diagnosis": "success",
        "recommended_next_action": "none",
        "request": {
            "url": "https://example.com/products",
            "cache_read_enabled": True,
            "output_format": "markdown",
            "skip_validation": False,
        },
        "attempts": [
            {
                "provider": "raw_http",
                "status": 200,
                "elapsed_ms": 120,
                "route": "raw_http",
                "result": "validation_failed",
                "block_type": "empty_content",
                "validation_detail": "The response body was empty.",
            },
            {
                "provider": "browserless",
                "status": 200,
                "elapsed_ms": 900,
                "route": "browserless:content+screenshot",
                "result": "success",
                "chars": 4200,
            },
        ],
        "skipped": ["scrapedrive(bad history)"],
        "final": {
            "provider": "browserless",
            "status": 200,
            "route": "browserless:content+screenshot",
            "chars": 4200,
            "markdown_chars": 1800,
            "content_validated": True,
        },
        "evaluation": {
            "status": "completed",
            "verdict": "fail",
            "needs_human_review": True,
            "elapsed_ms": 260,
            "model": "google/gemini-3.1-flash-lite",
            "root_cause": "missing prices",
            "recommended_action": "retry_rendered",
        },
    }
    (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (run_dir / "attempts.jsonl").write_text("", encoding="utf-8")
    (evaluation_dir / "final.md").write_text("# Products", encoding="utf-8")

    app = create_console_app(
        get_gateway=lambda: FakeGateway(ScrapeResult("https://example.com", "fake", True)),
        get_config=lambda: _config(tmp_path),
    )

    async with _client(app) as client:
        response = await client.get(f"/api/runs/{run_id}")

    assert response.status_code == 200
    trace = response.json()["trace"]
    assert trace == {
        **trace,
        "run_id": run_id,
        "status": "ok",
        "audit_verdict": "fail",
        "duration_ms": 1500,
    }
    assert len({step["id"] for step in trace["steps"]}) == len(trace["steps"])
    assert [step["kind"] for step in trace["steps"]] == [
        "request",
        "cache",
        "provider",
        "validation",
        "provider",
        "validation",
        "provider",
        "transform",
        "evaluation",
        "result",
        "persistence",
    ]

    raw_http = next(step for step in trace["steps"] if step["id"] == "provider-1")
    raw_validation = next(step for step in trace["steps"] if step["id"] == "provider-1-validation")
    browserless = next(step for step in trace["steps"] if step["id"] == "provider-2")
    evaluation = next(step for step in trace["steps"] if step["kind"] == "evaluation")
    persistence = next(step for step in trace["steps"] if step["kind"] == "persistence")

    assert raw_http["status"] == "error"
    assert raw_http["duration_ms"] == 120
    assert raw_http["timing"] == "recorded"
    assert raw_validation["parent_id"] == raw_http["id"]
    assert raw_validation["outcome"] == "rejected"
    assert raw_validation["duration_ms"] is None
    assert raw_validation["timing"] == "order_only"
    assert browserless["status"] == "ok"
    assert browserless["offset_ms"] == 120
    assert evaluation["status"] == "warning"
    assert evaluation["duration_ms"] == 260
    assert persistence["attributes"]["artifact_count"] == 3


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
    asset_version = hashlib.sha256(css.content + b"\0" + script.content).hexdigest()[:12]
    assert f"/assets/app.css?v={asset_version}" in page.text
    assert f"/assets/app.js?v={asset_version}" in page.text
    assert page.headers["cache-control"] == "no-cache"


async def test_console_shell_exposes_a_dense_trace_explorer(tmp_path: Path) -> None:
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
        "new-scrape-button",
        "new-scrape-dialog",
        "settings-button",
        "settings-dialog",
        "settings-form",
        "provider-settings-list",
        "default-timeout-input",
        "evaluation-timeout-input",
        "scrape-form",
        "url-input",
        "evaluation-goal",
        "live-toggle",
        "run-list",
        "trace-workspace",
        "trace-timeline",
        "step-inspector",
        "output-panel",
        "evaluation-panel",
        "visual-panel",
        "visual-viewer",
        "visual-subtitle",
        "artifacts-panel",
        "artifact-viewer",
        "raw-panel",
    ):
        assert f'id="{element_id}"' in page.text
    assert "Trace timeline" in page.text
    assert "ambient-grid" not in page.text
    assert "metric-card" not in page.text
    assert "sessionStorage" in script.text
    assert 'fetchJson("/api/runs' in script.text
    assert 'fetchJson("/api/evaluations' in script.text
    assert "renderTraceTimeline" in script.text
    assert "renderStepInspector" in script.text
    assert "renderVisual" in script.text
    assert "screenshotArtifact" in script.text
    assert "restoreActiveScrape" in script.text
    assert "runsPayload.active_runs" in script.text
    assert "updateLiveRunClock" in script.text
    assert "data-live-elapsed" in script.text
    assert 'classList.toggle("is-pending"' in script.text
    assert ".workspace-content.is-pending" in css.text
    assert 'fetchJson("/api/settings")' in script.text
    assert 'fetchJson("/api/settings", { method: "PUT"' in script.text
    assert "renderProviderSettings" in script.text
    assert "setInterval" in script.text
    assert "textContent" in script.text
    assert "grid-template-columns: 320px minmax(0, 1fr)" in css.text
    assert "@media (max-width: 900px)" in css.text
    assert "font-size: 12px" not in css.text
    assert "prefers-reduced-motion" in css.text


async def test_console_keeps_the_launched_trace_focused_when_it_completes(tmp_path: Path) -> None:
    from scrape_gateway.web import create_console_app

    app = create_console_app(
        get_gateway=lambda: FakeGateway(ScrapeResult("https://example.com", "fake", True)),
        get_config=lambda: _config(tmp_path),
    )

    async with _client(app) as client:
        script = (await client.get("/assets/app.js")).text

    assert "refreshRequestId" in script
    assert "selectionRequestId" in script
    assert "announceRunOutcome" in script
    assert "preferredRunId" in script
    assert "userInitiated" in script
    assert "launch?.watching" in script
    assert "refreshId !== state.refreshRequestId" in script


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
