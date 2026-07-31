from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from rich.console import Console
from typer.testing import CliRunner

from scrape_gateway.cli import _build_gateway, _print_result, app
from scrape_gateway.config import StrategyConfig
from scrape_gateway.memory import DomainMemory
from scrape_gateway.models import AttemptLedgerEntry, FailureReason, ScrapeRequest, ScrapeResult

runner = CliRunner()


def test_preferred_provider_preserves_configured_cost_ceiling():
    gateway = SimpleNamespace(
        strategy=StrategyConfig(mode="cheapest_successful", max_cost_per_url=6)
    )

    with patch(
        "scrape_gateway.cli.ScrapeGateway.from_config",
        return_value=gateway,
    ):
        result = _build_gateway("scrapedrive")

    assert result is gateway
    assert result.strategy.provider == "scrapedrive"
    assert result.strategy.mode == "cheapest_successful"
    assert result.strategy.max_cost_per_url == 6


def _fake_result(url: str) -> ScrapeResult:
    return ScrapeResult(
        url=url,
        provider="mock",
        success=True,
        status_code=200,
        html="<html><body>ok content here to pass validation</body></html>",
        route="mock",
    )


def _run_url(*args: str) -> tuple:
    """Invoke `sgw url` and capture the ScrapeRequest passed to gateway.scrape."""
    captured = {}

    async def fake_scrape(request, *, use_cache=True, use_memory=True):
        captured["request"] = request
        return _fake_result(request.url)

    with (
        patch("scrape_gateway.cli._build_gateway") as mock_gw,
    ):
        gw = mock_gw.return_value
        gw.scrape = AsyncMock(side_effect=fake_scrape)
        result = runner.invoke(app, ["url", *args])

    return result, captured.get("request")


def test_tier_flag_sets_metadata():
    result, req = _run_url("https://example.com", "--tier", "advanced")
    assert result.exit_code == 0
    assert req.metadata["start_tier"] == "scrapedrive:advanced"


def test_tier_flag_hyperdrive():
    result, req = _run_url("https://example.com", "-t", "hyperdrive")
    assert result.exit_code == 0
    assert req.metadata["start_tier"] == "scrapedrive:hyperdrive"


def test_no_tier_flag_empty_metadata():
    result, req = _run_url("https://example.com")
    assert result.exit_code == 0
    assert "start_tier" not in req.metadata


def test_debug_artifacts_flag_sets_metadata():
    result, req = _run_url("https://example.com", "--debug-artifacts")
    assert result.exit_code == 0
    assert req.metadata["debug_artifacts"] is True


def test_evaluation_goal_flag_sets_metadata():
    result, req = _run_url(
        "https://example.com/products",
        "--evaluation-goal",
        "Capture every visible product and price",
    )
    assert result.exit_code == 0
    assert req.metadata["evaluation_goal"] == "Capture every visible product and price"


def test_url_output_writes_selected_content_and_reports_path(tmp_path):
    output_path = tmp_path / "page.md"
    output_path.write_text("stale content")

    async def fake_scrape(request, *, use_cache=True, use_memory=True):
        return ScrapeResult(
            url=request.url,
            provider="mock",
            success=True,
            status_code=200,
            html="<main>HTML result</main>",
            markdown="# Markdown result",
            route="mock",
        )

    with patch("scrape_gateway.cli._build_gateway") as mock_gw:
        mock_gw.return_value.scrape = AsyncMock(side_effect=fake_scrape)
        result = runner.invoke(
            app,
            ["url", "https://example.com", "--format", "markdown", "-o", str(output_path)],
        )

    assert result.exit_code == 0
    assert output_path.read_text() == "# Markdown result"
    assert "Wrote scrape content to" in result.output
    assert str(output_path) in result.output


def test_url_output_rejects_missing_parent_directory(tmp_path):
    output_path = tmp_path / "missing" / "page.html"

    result, request = _run_url("https://example.com", "--output", str(output_path))

    assert result.exit_code == 2
    assert "Output directory does not exist" in result.output
    assert request is None
    assert not output_path.exists()


def test_url_screenshot_path_writes_image_and_surfaces_path(tmp_path):
    screenshot_path = tmp_path / "requested.jpg"
    captured = {}

    async def fake_scrape(request, *, use_cache=True, use_memory=True):
        captured["request"] = request
        return ScrapeResult(
            url=request.url,
            provider="mock",
            success=True,
            status_code=200,
            html="<main>Captured page</main>",
            screenshot=b"\xff\xd8\xffrequested-image",
            route="mock",
        )

    with patch("scrape_gateway.cli._build_gateway") as mock_gw:
        mock_gw.return_value.scrape = AsyncMock(side_effect=fake_scrape)
        result = runner.invoke(
            app,
            ["url", "https://example.com", "--screenshot", str(screenshot_path)],
        )

    assert result.exit_code == 0
    assert captured["request"].screenshot is True
    assert screenshot_path.read_bytes() == b"\xff\xd8\xffrequested-image"
    assert "SUCCESS" in result.output
    assert "screenshot" in result.output
    assert str(screenshot_path) in result.output


def test_url_bare_screenshot_surfaces_telemetry_artifact(tmp_path):
    screenshot_path = tmp_path / "runs" / "run-123" / "screenshot.png"
    screenshot_path.parent.mkdir(parents=True)
    screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\ntelemetry-image")
    captured = {}

    async def fake_scrape(request, *, use_cache=True, use_memory=True):
        captured["request"] = request
        return ScrapeResult(
            url=request.url,
            provider="mock",
            success=True,
            status_code=200,
            html="<main>Captured page</main>",
            screenshot=screenshot_path.read_bytes(),
            route="mock",
            metadata={"artifacts": {"screenshot": str(screenshot_path)}},
        )

    with patch("scrape_gateway.cli._build_gateway") as mock_gw:
        mock_gw.return_value.scrape = AsyncMock(side_effect=fake_scrape)
        result = runner.invoke(app, ["url", "https://example.com", "--screenshot"])

    assert result.exit_code == 0
    assert captured["request"].screenshot is True
    assert "screenshot" in result.output
    assert str(screenshot_path) in result.output


def test_url_bare_screenshot_warns_when_telemetry_did_not_save():
    async def fake_scrape(request, *, use_cache=True, use_memory=True):
        return ScrapeResult(
            url=request.url,
            provider="mock",
            success=True,
            status_code=200,
            html="<main>Captured page</main>",
            screenshot=b"\x89PNG\r\n\x1a\nunsaved-image",
            route="mock",
        )

    with patch("scrape_gateway.cli._build_gateway") as mock_gw:
        mock_gw.return_value.scrape = AsyncMock(side_effect=fake_scrape)
        result = runner.invoke(app, ["url", "https://example.com", "--screenshot"])

    assert result.exit_code == 0
    assert "Screenshot was captured but not saved because telemetry is disabled" in result.output
    assert "--screenshot PATH" in result.output


def test_url_screenshot_warns_when_provider_captures_nothing():
    result, request = _run_url("https://example.com", "--screenshot")

    assert result.exit_code == 0
    assert request.screenshot is True
    assert "Screenshot requested, but no image was captured" in result.output


def test_url_screenshot_path_rejects_missing_parent_directory(tmp_path):
    screenshot_path = tmp_path / "missing" / "page.jpg"

    result, request = _run_url(
        "https://example.com",
        "--screenshot",
        str(screenshot_path),
    )

    assert result.exit_code == 2
    assert "Screenshot output directory does not exist" in result.output
    assert request is None
    assert not screenshot_path.exists()


def test_run_output_writes_each_successful_scrape_in_input_order(tmp_path):
    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("https://one.example\nhttps://two.example\n")
    output_path = tmp_path / "batch.html"

    async def fake_scrape(request):
        return ScrapeResult(
            url=request.url,
            provider="mock",
            success=True,
            status_code=200,
            html=f"<main>{request.url}</main>",
            route="mock",
        )

    with patch("scrape_gateway.cli._build_gateway") as mock_gw:
        mock_gw.return_value.scrape = AsyncMock(side_effect=fake_scrape)
        result = runner.invoke(app, ["run", str(urls_file), "--output", str(output_path)])

    assert result.exit_code == 0
    assert output_path.read_text() == (
        "<main>https://one.example</main>\n<main>https://two.example</main>"
    )
    assert "Wrote 2 scrape results to" in result.output
    assert str(output_path) in result.output


def test_run_screenshot_directory_writes_one_named_image_per_url(tmp_path):
    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("https://one.example\nhttps://two.example/products\n")
    screenshot_dir = tmp_path / "screenshots"
    screenshot_dir.mkdir()
    captured_requests = []

    async def fake_scrape(request):
        captured_requests.append(request)
        return ScrapeResult(
            url=request.url,
            provider="mock",
            success=True,
            status_code=200,
            html=f"<main>{request.url}</main>",
            screenshot=b"\xff\xd8\xffbatch-image",
            route="mock",
        )

    with patch("scrape_gateway.cli._build_gateway") as mock_gw:
        mock_gw.return_value.scrape = AsyncMock(side_effect=fake_scrape)
        result = runner.invoke(
            app,
            ["run", str(urls_file), "--screenshot", str(screenshot_dir)],
        )

    first = screenshot_dir / "001-one-example.jpg"
    second = screenshot_dir / "002-two-example-products.jpg"
    assert result.exit_code == 0
    assert [request.screenshot for request in captured_requests] == [True, True]
    assert first.read_bytes() == b"\xff\xd8\xffbatch-image"
    assert second.read_bytes() == b"\xff\xd8\xffbatch-image"
    assert "Saved screenshots" in result.output
    assert str(first) in result.output
    assert str(second) in result.output


def test_run_batch_total_sums_each_complete_run_ledger(tmp_path):
    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("https://one.example\nhttps://two.example\n")
    run_costs = iter([7, 9])

    async def fake_scrape(request):
        run_cost = next(run_costs)
        return ScrapeResult(
            url=request.url,
            provider="winner",
            success=True,
            status_code=200,
            html="<main>complete result</main>",
            cost_units=5,
            route="winner",
            attempt_ledger=[
                AttemptLedgerEntry(
                    provider="fallback",
                    route="fallback",
                    cost_units=run_cost - 5,
                    cost_provenance="estimated",
                    success=False,
                    latency_ms=10,
                    status_code=403,
                    failure_reason=FailureReason.HTTP_403,
                    block_type=None,
                ),
                AttemptLedgerEntry(
                    provider="winner",
                    route="winner",
                    cost_units=5,
                    cost_provenance="estimated",
                    success=True,
                    latency_ms=20,
                    status_code=200,
                    failure_reason=None,
                    block_type=None,
                ),
            ],
        )

    with patch("scrape_gateway.cli._build_gateway") as mock_gw:
        mock_gw.return_value.scrape = AsyncMock(side_effect=fake_scrape)
        result = runner.invoke(app, ["run", str(urls_file)])

    assert result.exit_code == 0
    assert "cost 16" in result.output


def test_run_batch_surfaces_budget_exceeded_status(tmp_path):
    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("https://budget.example\n")

    async def fake_scrape(request):
        return ScrapeResult(
            url=request.url,
            provider="budget",
            success=False,
            failure_reason=FailureReason.BUDGET_EXCEEDED,
            error="Cost budget exhausted.",
            metadata={
                "budget_stop": {
                    "max_cost_per_url": 6.0,
                    "spent_cost_units": 2.0,
                    "remaining_cost_units": 4.0,
                    "next_provider": "expensive",
                    "next_attempt_cost_units": 5.0,
                }
            },
        )

    with patch("scrape_gateway.cli._build_gateway") as mock_gw:
        mock_gw.return_value.scrape = AsyncMock(side_effect=fake_scrape)
        result = runner.invoke(app, ["run", str(urls_file)])

    assert result.exit_code == 0
    assert "budget_exceeded" in result.output


def _record_cli_cost_fixture(db_path) -> None:
    memory = DomainMemory(db_path)
    recorded_at = datetime.now(timezone.utc)
    memory.record_attempt_ledger(
        "run-example",
        ScrapeRequest("https://example.com/products", render_js=True),
        [
            AttemptLedgerEntry(
                provider="raw_http",
                route="raw_http",
                cost_units=2,
                cost_provenance="estimated",
                success=False,
                latency_ms=50,
                status_code=403,
                failure_reason=FailureReason.HTTP_403,
                block_type="cloudflare",
            ),
            AttemptLedgerEntry(
                provider="scrapfly",
                route="scrapfly:asp",
                cost_units=5,
                cost_provenance="exact",
                success=True,
                latency_ms=100,
                status_code=200,
                failure_reason=None,
                block_type=None,
            ),
        ],
        recorded_at=recorded_at,
    )
    memory.record_attempt_ledger(
        "run-other",
        ScrapeRequest("https://other.example/about"),
        [
            AttemptLedgerEntry(
                provider="raw_http",
                route="raw_http",
                cost_units=3,
                cost_provenance="estimated",
                success=True,
                latency_ms=25,
                status_code=200,
                failure_reason=None,
                block_type=None,
            )
        ],
        recorded_at=recorded_at,
    )


def test_cost_command_prints_json_spend_by_domain_and_provider(tmp_path):
    db_path = tmp_path / "memory.sqlite"
    _record_cli_cost_fixture(db_path)

    with patch(
        "scrape_gateway.config.load_config",
        return_value=SimpleNamespace(memory_path=str(db_path)),
    ):
        result = runner.invoke(app, ["cost", "--days", "7", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["days"] == 7
    assert payload["domain"] is None
    assert payload["totals"] == {
        "attempt_count": 3,
        "successful_attempt_count": 2,
        "failed_attempt_count": 1,
        "successful_attempt_cost_units": 8.0,
        "failed_attempt_cost_units": 2.0,
        "total_cost_units": 10.0,
    }
    assert payload["by_domain"] == [
        {
            "domain": "example.com",
            "attempt_count": 2,
            "successful_attempt_count": 1,
            "failed_attempt_count": 1,
            "successful_attempt_cost_units": 5.0,
            "failed_attempt_cost_units": 2.0,
            "total_cost_units": 7.0,
        },
        {
            "domain": "other.example",
            "attempt_count": 1,
            "successful_attempt_count": 1,
            "failed_attempt_count": 0,
            "successful_attempt_cost_units": 3.0,
            "failed_attempt_cost_units": 0.0,
            "total_cost_units": 3.0,
        },
    ]
    assert payload["by_provider"] == [
        {
            "provider": "raw_http",
            "attempt_count": 2,
            "successful_attempt_count": 1,
            "failed_attempt_count": 1,
            "successful_attempt_cost_units": 3.0,
            "failed_attempt_cost_units": 2.0,
            "total_cost_units": 5.0,
        },
        {
            "provider": "scrapfly",
            "attempt_count": 1,
            "successful_attempt_count": 1,
            "failed_attempt_count": 0,
            "successful_attempt_cost_units": 5.0,
            "failed_attempt_cost_units": 0.0,
            "total_cost_units": 5.0,
        },
    ]


def test_cost_command_rich_output_filters_domain_and_separates_failed_spend(tmp_path):
    db_path = tmp_path / "memory.sqlite"
    _record_cli_cost_fixture(db_path)

    with patch(
        "scrape_gateway.config.load_config",
        return_value=SimpleNamespace(memory_path=str(db_path)),
    ):
        result = runner.invoke(
            app,
            ["cost", "--days", "7", "--domain", "https://www.example.com/path"],
        )

    assert result.exit_code == 0
    assert "Scrape Cost — Last 7 Days" in result.output
    assert "Spend by Domain" in result.output
    assert "Spend by Provider" in result.output
    assert "Successful spend" in result.output
    assert "Failed spend" in result.output
    assert "example.com" in result.output
    assert "raw_http" in result.output
    assert "scrapfly" in result.output
    assert "other.example" not in result.output


def test_cost_command_handles_empty_ledger(tmp_path):
    db_path = tmp_path / "memory.sqlite"
    DomainMemory(db_path)

    with patch(
        "scrape_gateway.config.load_config",
        return_value=SimpleNamespace(memory_path=str(db_path)),
    ):
        result = runner.invoke(app, ["cost", "--days", "30"])

    assert result.exit_code == 0
    assert "No cost ledger entries found" in result.output


def test_print_result_surfaces_failed_audit_without_marking_scrape_failed(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(
        "scrape_gateway.cli.console",
        Console(file=output, force_terminal=False, color_system=None),
    )
    result = _fake_result("https://example.com/products")
    result.metadata["evaluation"] = {
        "status": "completed",
        "verdict": "fail",
        "needs_human_review": True,
        "recommended_action": "retry_with_wait",
    }

    _print_result(result)

    rendered = output.getvalue()
    assert "SUCCESS" in rendered
    assert "AI audit" in rendered
    assert "fail" in rendered
    assert "human review" in rendered
    assert "retry_with_wait" in rendered


def test_print_result_surfaces_budget_stop_details(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(
        "scrape_gateway.cli.console",
        Console(file=output, force_terminal=False, color_system=None),
    )
    result = ScrapeResult(
        url="https://example.com/products",
        provider="budget",
        success=False,
        failure_reason=FailureReason.BUDGET_EXCEEDED,
        error="Cost budget exhausted before expensive_provider.",
        metadata={
            "budget_stop": {
                "max_cost_per_url": 6.0,
                "spent_cost_units": 2.0,
                "remaining_cost_units": 4.0,
                "next_provider": "expensive_provider",
                "next_attempt_cost_units": 5.0,
            }
        },
    )

    _print_result(result)

    rendered = output.getvalue()
    assert "FAILED" in rendered
    assert "budget_exceeded" in rendered
    assert "spent 2 / 6 units" in rendered
    assert "expensive_provider needs 5" in rendered


def test_evaluations_command_prints_aggregate_json(tmp_path):
    run_dir = tmp_path / "runs" / "run-123"
    run_dir.mkdir(parents=True)
    (run_dir / "report.json").write_text(
        json.dumps(
            {
                "run_id": "run-123",
                "started_at": "2026-07-16T10:00:00+00:00",
                "url": "https://example.com/products",
                "domain": "example.com",
                "evaluation": {
                    "status": "completed",
                    "model": "google/gemini-3.1-flash-lite",
                    "provider": "Google Vertex",
                    "prompt_version": "scrape-usability-v2",
                    "verdict": "fail",
                    "needs_human_review": False,
                    "root_cause": "incomplete_content",
                    "recommended_action": "retry_provider",
                    "checks": {
                        "access": {
                            "result": "pass",
                            "evidence": "The page is accessible.",
                        },
                        "goal_coverage": {
                            "result": "fail",
                            "evidence": "The listing stops early.",
                        },
                        "extractability": {
                            "result": "fail",
                            "evidence": "Only part of the listing was extracted.",
                        },
                        "visual_state": {
                            "result": "not_applicable",
                            "evidence": "No screenshot was supplied.",
                        },
                    },
                    "issues": [
                        {
                            "code": "truncated_content",
                            "severity": "high",
                            "source": "markdown",
                            "evidence": "The listing stops abruptly.",
                        }
                    ],
                    "improvement_opportunities": ["Try a rendered provider."],
                    "usage": {"cost": 0.0003, "total_tokens": 800},
                    "cached": False,
                },
            }
        )
    )
    newer_run_dir = tmp_path / "runs" / "run-without-evaluation"
    newer_run_dir.mkdir(parents=True)
    (newer_run_dir / "report.json").write_text(
        json.dumps(
            {
                "run_id": "run-without-evaluation",
                "started_at": "2026-07-16T11:00:00+00:00",
                "url": "https://example.com/about",
                "domain": "example.com",
            }
        )
    )

    result = runner.invoke(
        app,
        [
            "evaluations",
            "--root",
            str(tmp_path / "runs"),
            "--limit",
            "1",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["runs_scanned"] == 1
    assert payload["verdict_counts"] == {"fail": 1}
    assert payload["review_queue"][0]["run_id"] == "run-123"


def test_calibrate_evaluator_command_replays_recorded_responses(tmp_path):
    corpus_root = tmp_path / "v1"
    capture_root = corpus_root / "captures"
    responses_root = tmp_path / "responses"
    response_dir = responses_root / "baseline" / "google__gemini-test"
    capture_root.mkdir(parents=True)
    response_dir.mkdir(parents=True)
    html = "<html><main>" + "Complete article content. " * 8 + "</main></html>"
    (capture_root / "case.html.gz").write_bytes(gzip.compress(html.encode("utf-8"), mtime=0))
    (corpus_root / "cases.json").write_text(
        json.dumps(
            [
                {
                    "id": "case",
                    "classification": "clean_article",
                    "human_verdict": "pass",
                    "human_root_cause": "none",
                    "human_issue_codes": [],
                    "split": "dev",
                    "score": True,
                    "artifact": "captures/case.html.gz",
                    "status_code": 200,
                }
            ]
        )
    )
    (response_dir / "case.json").write_text(
        json.dumps(
            {
                "case_id": "case",
                "model": "google/gemini-test",
                "prompt_version": "scrape-usability-v2",
                "status": "completed",
                "elapsed_ms": 100,
                "usage": {"cost": 0.0001},
                "judgment": {
                    "verdict": "pass",
                    "root_cause": "none",
                    "issues": [],
                    "needs_human_review": False,
                },
            }
        )
    )

    result = runner.invoke(
        app,
        [
            "calibrate-evaluator",
            "--corpus-root",
            str(corpus_root),
            "--responses-root",
            str(responses_root),
            "--run-name",
            "baseline",
            "--model",
            "google/gemini-test",
            "--split",
            "dev",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["mode"] == "offline"
    assert payload["metrics"]["verdict"]["tpr"] == 1.0
    assert payload["selective_gate"]["version"] == "selective-v1"
    assert [row["policy"] for row in payload["policy_comparison"]] == [
        "off",
        "audit",
        "selective",
    ]
    assert payload["response_dir"] == str(response_dir)


def test_url_exits_nonzero_on_failure():
    async def fake_scrape(request, *, use_cache=True, use_memory=True):
        return ScrapeResult(
            url=request.url,
            provider="raw_http",
            success=False,
            error="407 Proxy Authentication Required",
            failure_reason=FailureReason.PROXY_ERROR,
            route="raw_http",
        )

    with patch("scrape_gateway.cli._build_gateway") as mock_gw:
        gw = mock_gw.return_value
        gw.scrape = AsyncMock(side_effect=fake_scrape)
        result = runner.invoke(app, ["url", "https://example.com"])

    assert result.exit_code == 1
    assert "proxy_error" in result.output
    assert "407 Proxy Authentication Required" in result.output


def test_extract_og_meta_basic():
    from scrape_gateway.cli import _extract_og_meta

    html = """<html><head>
    <title>Fallback Title</title>
    <meta property="og:title" content="My Page">
    <meta property="og:description" content="A description">
    <meta property="og:image" content="https://example.com/img.png">
    <meta property="og:url" content="https://example.com">
    <meta property="og:type" content="website">
    <meta property="og:site_name" content="Example">
    <meta name="description" content="ignored">
    </head><body>ok</body></html>"""
    og = _extract_og_meta(html)
    assert og["og:title"] == "My Page"
    assert og["og:description"] == "A description"
    assert og["og:image"] == "https://example.com/img.png"
    assert og["og:type"] == "website"
    assert "description" not in og


def test_extract_og_meta_title_fallback():
    from scrape_gateway.cli import _extract_og_meta

    html = "<html><head><title>Just a Title</title></head><body>ok</body></html>"
    og = _extract_og_meta(html)
    assert og["og:title"] == "Just a Title"


def test_extract_og_meta_empty():
    from scrape_gateway.cli import _extract_og_meta

    og = _extract_og_meta("<html><body>nothing</body></html>")
    assert og == {}


def test_extract_page_metadata_combines_social_structured_and_document_metadata():
    from scrape_gateway.cli import _extract_page_metadata

    html = """<html><head>
    <meta charset="UTF-8">
    <meta property="og:title" content="OpenGraph title">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="Twitter title">
    <meta property="twitter:image" content="https://cdn.example.com/card.jpg">
    <meta name="robots" content="index, follow">
    <link rel="canonical" href="/articles/canonical">
    <link rel="shortcut icon" href="/favicon.ico">
    <link rel="apple-touch-icon" href="https://cdn.example.com/touch.png">
    <script type="application/ld+json">
      {"@context": "https://schema.org", "@type": "Article", "headline": "Story"}
    </script>
    </head><body>ok</body></html>"""

    metadata = _extract_page_metadata(html, "https://example.com/articles/story")

    assert metadata == {
        "og:title": "OpenGraph title",
        "twitter:card": "summary_large_image",
        "twitter:title": "Twitter title",
        "twitter:image": "https://cdn.example.com/card.jpg",
        "json_ld": [
            {
                "@context": "https://schema.org",
                "@type": "Article",
                "headline": "Story",
            }
        ],
        "canonical": "https://example.com/articles/canonical",
        "favicon": "https://example.com/favicon.ico",
        "apple_touch_icon": "https://cdn.example.com/touch.png",
        "charset": "UTF-8",
        "robots": "index, follow",
    }


def test_extract_page_metadata_skips_invalid_json_ld_and_reads_content_type_charset():
    from scrape_gateway.cli import _extract_page_metadata

    html = """<html><head>
    <meta http-equiv="Content-Type" content="text/html; charset=windows-1252">
    <script type="application/ld+json">not valid JSON</script>
    <script type="application/ld+json">[{"@type": "Product"}]</script>
    </head><body>ok</body></html>"""

    metadata = _extract_page_metadata(html)

    assert metadata["charset"] == "windows-1252"
    assert metadata["json_ld"] == [[{"@type": "Product"}]]


def test_meta_command_prints_non_opengraph_metadata():
    html_with_metadata = """<html><head>
    <meta name="twitter:card" content="summary">
    <link rel="canonical" href="https://example.com/canonical">
    </head><body>ok content here to pass validation</body></html>"""

    async def fake_scrape(request, *, use_cache=True, use_memory=True):
        return ScrapeResult(
            url=request.url,
            provider="mock",
            success=True,
            status_code=200,
            html=html_with_metadata,
            route="mock",
        )

    with patch("scrape_gateway.cli._build_gateway") as mock_gw:
        gw = mock_gw.return_value
        gw.scrape = AsyncMock(side_effect=fake_scrape)
        result = runner.invoke(app, ["meta", "https://example.com/page"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "twitter:card": "summary",
        "canonical": "https://example.com/canonical",
    }


def test_meta_flag_prints_json():
    html_with_og = """<html><head>
    <meta property="og:title" content="Test OG">
    <meta property="og:type" content="article">
    </head><body>ok content here to pass validation</body></html>"""

    async def fake_scrape(request, *, use_cache=True, use_memory=True):
        return ScrapeResult(
            url=request.url,
            provider="mock",
            success=True,
            status_code=200,
            html=html_with_og,
            route="mock",
        )

    with patch("scrape_gateway.cli._build_gateway") as mock_gw:
        gw = mock_gw.return_value
        gw.scrape = AsyncMock(side_effect=fake_scrape)
        result = runner.invoke(app, ["url", "https://example.com", "--meta"])

    assert result.exit_code == 0
    assert '"og:title": "Test OG"' in result.output
    assert '"og:type": "article"' in result.output


def test_meta_flag_forces_html_when_markdown():
    captured = {}

    async def fake_scrape(request, *, use_cache=True, use_memory=True):
        captured["format"] = request.output_format
        return ScrapeResult(
            url=request.url,
            provider="mock",
            success=True,
            status_code=200,
            html="<html><head><meta property='og:title' content='X'></head><body>ok content</body></html>",
            route="mock",
        )

    with patch("scrape_gateway.cli._build_gateway") as mock_gw:
        gw = mock_gw.return_value
        gw.scrape = AsyncMock(side_effect=fake_scrape)
        result = runner.invoke(app, ["url", "https://example.com", "--meta", "-f", "markdown"])

    assert result.exit_code == 0
    assert captured["format"] == "html"


def test_meta_command_no_js_by_default():
    captured = {}

    async def fake_scrape(request, *, use_cache=True, use_memory=True):
        captured["render_js"] = request.render_js
        return ScrapeResult(
            url=request.url,
            provider="mock",
            success=True,
            status_code=200,
            html='<html><head><meta property="og:title" content="FB Post"><meta property="og:image" content="https://fb.com/img.jpg"></head><body>ok content</body></html>',
            route="mock",
        )

    with patch("scrape_gateway.cli._build_gateway") as mock_gw:
        gw = mock_gw.return_value
        gw.scrape = AsyncMock(side_effect=fake_scrape)
        result = runner.invoke(app, ["meta", "https://facebook.com/some/post"])

    assert result.exit_code == 0
    assert captured["render_js"] is False
    assert '"og:title": "FB Post"' in result.output
    assert '"og:image": "https://fb.com/img.jpg"' in result.output


def test_meta_command_render_js_flag():
    captured = {}

    async def fake_scrape(request, *, use_cache=True, use_memory=True):
        captured["render_js"] = request.render_js
        return ScrapeResult(
            url=request.url,
            provider="mock",
            success=True,
            status_code=200,
            html='<html><head><meta property="og:title" content="SPA Title"></head><body>ok content</body></html>',
            route="mock",
        )

    with patch("scrape_gateway.cli._build_gateway") as mock_gw:
        gw = mock_gw.return_value
        gw.scrape = AsyncMock(side_effect=fake_scrape)
        result = runner.invoke(app, ["meta", "https://example.com", "--render-js"])

    assert result.exit_code == 0
    assert captured["render_js"] is True


def test_telemetry_command_prints_recent_reports():
    report = {
        "started_at": "2026-05-23T19:00:00+00:00",
        "success": True,
        "domain": "example.com",
        "diagnosis": "success",
        "recommended_next_action": "none",
        "final": {"provider": "raw_http"},
        "_path": ".scrape-gateway/runs/abc/report.json",
    }
    with patch("scrape_gateway.telemetry.load_recent_reports", return_value=[report]):
        result = runner.invoke(app, ["telemetry"])

    assert result.exit_code == 0
    assert "example.com" in result.output
    assert "success" in result.output


def test_telemetry_summary_prints_actionable_aggregates():
    reports = [
        {
            "domain": "example.com",
            "success": True,
            "diagnosis": "success",
            "attempts": [
                {"provider": "header_capture", "cost": 0},
                {"provider": "raw_http", "cost": 0},
            ],
            "final": {"provider": "raw_http"},
        },
        {
            "domain": "example.com",
            "success": False,
            "diagnosis": "validator_rejected",
            "attempts": [{"provider": "raw_http", "cost": 0}],
            "final": {"provider": "raw_http"},
        },
    ]
    with (
        patch("scrape_gateway.telemetry.load_recent_reports", return_value=reports),
        patch(
            "scrape_gateway.discovery.discover_providers",
            return_value={"raw_http": object},
        ),
    ):
        result = runner.invoke(app, ["telemetry", "--summary"])

    assert result.exit_code == 0
    assert "Telemetry Summary — Last 20 Runs" in result.output
    assert "Success rate" in result.output
    assert "50.0%" in result.output
    assert "validator_rejected" in result.output
    assert "Provider Hit Rate" in result.output
    assert "raw_http" in result.output
    assert "header_capture" not in result.output
    assert "1 non-provider record omitted" in result.output


def test_cache_key_differs_by_render_js():
    from scrape_gateway.cache import ArtifactCache

    cache = ArtifactCache()
    key_plain = cache.key_for_url("https://example.com", render_js=False)
    key_js = cache.key_for_url("https://example.com", render_js=True)
    assert key_plain != key_js
