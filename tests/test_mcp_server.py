from __future__ import annotations

from unittest.mock import AsyncMock, patch

from scrape_gateway.models import ScrapeResult


async def test_scrape_tool_forwards_evaluation_goal_and_returns_audit_pointer() -> None:
    from scrape_gateway.mcp_server import scrape

    captured = {}

    async def fake_scrape(request, *, use_cache=True, use_memory=True):
        captured["request"] = request
        return ScrapeResult(
            url=request.url,
            provider="browserless",
            success=True,
            status_code=200,
            markdown="# Products\n\nWidget — $19.99",
            screenshot=b"png",
            metadata={
                "run_id": "run-mcp-123",
                "telemetry_report": "/runs/run-mcp-123/report.json",
                "evaluation": {
                    "status": "completed",
                    "verdict": "pass",
                    "needs_human_review": False,
                    "recommended_action": "accept",
                },
            },
        )

    with patch("scrape_gateway.mcp_server._get_gateway") as get_gateway:
        get_gateway.return_value.scrape = AsyncMock(side_effect=fake_scrape)
        response = await scrape(
            "https://example.com/products",
            screenshot=True,
            evaluation_goal="Capture visible products and prices",
        )

    request = captured["request"]
    assert request.screenshot is True
    assert request.metadata["evaluation_goal"] == "Capture visible products and prices"
    assert response["evaluation"]["verdict"] == "pass"
    assert response["run_id"] == "run-mcp-123"
    assert response["telemetry_report"] == "/runs/run-mcp-123/report.json"
