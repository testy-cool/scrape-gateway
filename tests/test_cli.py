from __future__ import annotations

import json
from io import StringIO
from unittest.mock import AsyncMock, patch

from rich.console import Console
from typer.testing import CliRunner

from scrape_gateway.cli import _print_result, app
from scrape_gateway.models import FailureReason, ScrapeResult

runner = CliRunner()


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
