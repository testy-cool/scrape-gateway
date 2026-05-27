from __future__ import annotations

from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from scrape_gateway.cli import app
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


def test_cache_key_differs_by_render_js():
    from scrape_gateway.cache import ArtifactCache

    cache = ArtifactCache()
    key_plain = cache.key_for_url("https://example.com", render_js=False)
    key_js = cache.key_for_url("https://example.com", render_js=True)
    assert key_plain != key_js
