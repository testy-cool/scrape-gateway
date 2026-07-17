from __future__ import annotations

import ddgs
from typer.testing import CliRunner

from scrape_gateway.cli import app
from scrape_gateway.mcp_server import search as mcp_search


class FakeDDGS:
    def __init__(self, proxy: str | None = None) -> None:
        self.proxy = proxy

    def text(self, query: str, **kwargs) -> list[dict[str, str]]:
        return [
            {
                "title": f"Result for {query}",
                "href": "https://example.com/result",
                "body": "Example search result",
            }
        ]


def test_cli_search_uses_declared_ddgs_dependency(monkeypatch) -> None:
    monkeypatch.setattr(ddgs, "DDGS", FakeDDGS)

    result = CliRunner().invoke(app, ["search", "test query", "--format", "json"])

    assert result.exit_code == 0, result.exception
    assert '"href": "https://example.com/result"' in result.stdout


async def test_mcp_search_uses_declared_ddgs_dependency(monkeypatch) -> None:
    monkeypatch.setattr(ddgs, "DDGS", FakeDDGS)

    results = await mcp_search("test query", max_results=1)

    assert results == [
        {
            "title": "Result for test query",
            "href": "https://example.com/result",
            "body": "Example search result",
        }
    ]
