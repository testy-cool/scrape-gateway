"""Scrape Gateway MCP Server — exposes sgw tools over Streamable HTTP."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from urllib.parse import urlparse

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.routing import Mount

_TOKEN = os.environ.get("SGW_MCP_TOKEN", "")
_PORT = int(os.environ.get("SGW_MCP_PORT", "8100"))
_HOST = os.environ.get("SGW_MCP_HOST", "0.0.0.0")
_PUBLIC_URL = os.environ.get("SGW_MCP_URL", f"http://localhost:{_PORT}")


def _csv_env(name: str) -> list[str]:
    value = os.environ.get(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _host_allowlist(public_url: str) -> list[str]:
    parsed = urlparse(public_url)
    hosts: list[str] = []

    if parsed.netloc:
        hosts.append(parsed.netloc)
    if parsed.hostname:
        hosts.append(parsed.hostname)
        hosts.append(f"{parsed.hostname}:*")

    hosts.extend(
        [
            f"localhost:{_PORT}",
            "localhost:*",
            f"127.0.0.1:{_PORT}",
            "127.0.0.1:*",
        ]
    )
    hosts.extend(_csv_env("SGW_MCP_ALLOWED_HOSTS"))
    return _dedupe(hosts)


def _origin_allowlist(public_url: str) -> list[str]:
    parsed = urlparse(public_url)
    origins: list[str] = []

    if parsed.scheme and parsed.netloc:
        origins.append(f"{parsed.scheme}://{parsed.netloc}")

    origins.extend(
        [
            f"http://localhost:{_PORT}",
            "http://localhost:*",
            f"http://127.0.0.1:{_PORT}",
            "http://127.0.0.1:*",
        ]
    )
    origins.extend(_csv_env("SGW_MCP_ALLOWED_ORIGINS"))
    return _dedupe(origins)


class _BearerVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        if not _TOKEN:
            return AccessToken(token=token, client_id="anonymous", scopes=["user"])
        if token == _TOKEN:
            return AccessToken(token=token, client_id="trusted", scopes=["user"])
        return None


_mcp_kwargs: dict = {
    "stateless_http": True,
    "json_response": True,
    "transport_security": TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_host_allowlist(_PUBLIC_URL),
        allowed_origins=_origin_allowlist(_PUBLIC_URL),
    ),
}
if _TOKEN:
    _mcp_kwargs["token_verifier"] = _BearerVerifier()
    _mcp_kwargs["auth"] = AuthSettings(
        issuer_url=AnyHttpUrl(_PUBLIC_URL),
        resource_server_url=AnyHttpUrl(_PUBLIC_URL),
        required_scopes=["user"],
    )

mcp = FastMCP("scrape-gateway", **_mcp_kwargs)


_gateway = None


def _get_gateway():
    global _gateway
    if _gateway is None:
        from .router import ScrapeGateway

        _gateway = ScrapeGateway.from_config()
    return _gateway


def _apply_console_settings(settings: dict):
    global _gateway

    from .config import load_config, save_operator_settings
    from .router import ScrapeGateway

    save_operator_settings(settings)
    config = load_config()
    _gateway = ScrapeGateway.from_config(config)
    return config


def _create_service_app() -> Starlette:
    from .web import create_console_routes

    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(application: Starlette) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield

    return Starlette(
        routes=[
            *create_console_routes(
                token=_TOKEN,
                get_gateway=_get_gateway,
                apply_settings=_apply_console_settings,
            ),
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
    )


@mcp.tool()
async def search(
    query: str,
    max_results: int = 10,
    region: str = "us-en",
    timelimit: str | None = None,
    backend: str = "auto",
    proxy: bool = False,
) -> list[dict]:
    """Search the web via DuckDuckGo.

    Args:
        query: Search query string
        max_results: Maximum number of results (default 10)
        region: Region code (us-en, uk-en, wt-wt=global, etc.)
        timelimit: Time filter - d(ay), w(eek), m(onth), y(ear)
        backend: Search backend - auto, bing, duckduckgo, google, brave
        proxy: Route through residential proxy (SCRAPE_PROXY_URL)
    """
    from ddgs import DDGS

    proxy_url = None
    if proxy:
        proxy_url = os.environ.get("SCRAPE_PROXY_URL")
        if not proxy_url:
            return [{"error": "SCRAPE_PROXY_URL not configured"}]

    ddgs = DDGS(proxy=proxy_url)
    results = ddgs.text(
        query,
        region=region,
        safesearch="moderate",
        timelimit=timelimit,
        max_results=max_results,
        backend=backend,
    )
    return results or []


@mcp.tool()
async def scrape(
    url: str,
    render_js: bool = False,
    country: str | None = None,
    output_format: str = "markdown",
    premium: bool = False,
    screenshot: bool = False,
    evaluation_goal: str | None = None,
) -> dict:
    """Scrape a URL and return its content.

    Tries providers from cheapest to most expensive until one succeeds.
    Results are cached so repeat scrapes are instant.

    Args:
        url: The URL to scrape
        render_js: Whether to render JavaScript (needed for SPAs)
        country: Country code for geo-targeted scraping
        output_format: "markdown" or "html"
        premium: Use premium/residential proxies
        screenshot: Capture screenshot evidence when a capable provider is available
        evaluation_goal: Describe what a usable scrape must contain for the audit evaluator
    """
    from .models import ScrapeRequest

    gateway = _get_gateway()
    result = await gateway.scrape(
        ScrapeRequest(
            url,
            country=country,
            render_js=render_js,
            premium=premium,
            screenshot=screenshot,
            output_format=output_format,
            metadata={"evaluation_goal": evaluation_goal} if evaluation_goal else {},
        ),
        use_cache=True,
        use_memory=True,
    )

    response = {
        "success": result.success,
        "url": result.url,
        "provider": result.provider,
        "status_code": result.status_code,
    }
    if result.success:
        if output_format == "markdown" and result.markdown:
            response["content"] = result.markdown
        elif result.html:
            response["content"] = result.html
        if result.markdown:
            response["content_length"] = len(result.markdown)
    else:
        response["error"] = result.error
        response["failure_reason"] = result.failure_reason.value if result.failure_reason else None

    for field in ("run_id", "telemetry_report", "evaluation"):
        if field in result.metadata:
            response[field] = result.metadata[field]

    return response


@mcp.tool()
async def links(
    url: str,
    render_js: bool = False,
    country: str | None = None,
) -> dict:
    """Extract and group all links from a page by semantic location.

    Finds all links on a page and groups them by where they appear
    (navigation, main content, footer, sidebar). Returns indexed links
    for further exploration.

    Args:
        url: The URL to extract links from
        render_js: Whether to render JavaScript first
        country: Country code for geo-targeted scraping
    """
    from .cli import _extract_links
    from .models import ScrapeRequest

    gateway = _get_gateway()
    result = await gateway.scrape(
        ScrapeRequest(url, country=country, render_js=render_js),
        use_cache=True,
        use_memory=True,
    )

    if not result.success or not result.html:
        return {
            "success": False,
            "error": result.error or result.failure_reason.value if result.failure_reason else "scrape failed",
        }

    all_links, groups = _extract_links(result.html, result.url)

    return {
        "success": True,
        "url": result.url,
        "total_links": len(all_links),
        "groups": {section: len(indices) for section, indices in groups.items()},
        "links": all_links,
    }


@mcp.tool()
async def detect(
    url: str,
    render_js: bool = False,
    country: str | None = None,
) -> dict:
    """Detect repeated elements and data patterns on a page.

    Finds product cards, article lists, search results, and other
    repeated structures. Also detects prices, emails, phones, dates.

    Args:
        url: The URL to analyze
        render_js: Whether to render JavaScript first
        country: Country code for geo-targeted scraping
    """
    from .cli import _detect_patterns
    from .models import ScrapeRequest

    gateway = _get_gateway()
    result = await gateway.scrape(
        ScrapeRequest(url, country=country, render_js=render_js),
        use_cache=True,
        use_memory=True,
    )

    if not result.success or not result.html:
        return {
            "success": False,
            "error": result.error or result.failure_reason.value if result.failure_reason else "scrape failed",
        }

    patterns = _detect_patterns(result.html)

    return {
        "success": True,
        "url": result.url,
        "patterns": patterns,
    }


@mcp.tool()
async def extract(
    url: str,
    selector: str | None = None,
    render_js: bool = False,
    country: str | None = None,
    limit: int = 0,
) -> dict:
    """Extract structured data from repeated page elements.

    Finds repeated elements (product cards, article lists) and pulls
    structured data from each one — titles, prices, images, links, dates.

    Args:
        url: The URL to extract data from
        selector: CSS selector (auto-detects if omitted)
        render_js: Whether to render JavaScript first
        country: Country code for geo-targeted scraping
        limit: Max rows to return (0=all)
    """
    from .cli import _extract_rows
    from .models import ScrapeRequest

    gateway = _get_gateway()
    result = await gateway.scrape(
        ScrapeRequest(url, country=country, render_js=render_js),
        use_cache=True,
        use_memory=True,
    )

    if not result.success or not result.html:
        return {
            "success": False,
            "error": result.error or result.failure_reason.value if result.failure_reason else "scrape failed",
        }

    rows, description = _extract_rows(result.html, selector)

    if limit > 0:
        rows = rows[:limit]

    return {
        "success": True,
        "url": result.url,
        "description": description,
        "row_count": len(rows),
        "rows": rows,
    }


@mcp.tool()
async def sitemap(
    url: str,
    lang: str | None = None,
    limit: int = 0,
    discover_only: bool = False,
) -> dict:
    """Find and expand sitemaps for a website.

    Discovers sitemap URLs from robots.txt, then expands them to get
    all page URLs listed. Uses sgw's provider pipeline for anti-bot bypass.

    Args:
        url: Site URL or domain to discover sitemaps for
        lang: Filter page URLs by language code (e.g. "en", "de")
        limit: Max page URLs to return (0=all)
        discover_only: Only return sitemap XML URLs, don't expand to pages
    """
    from sg_sitemap import (
        _base_url,
        _discover_sitemaps,
        _expand_sitemaps,
        _normalize_url,
    )

    gateway = _get_gateway()
    normalized = _normalize_url(url)
    base = _base_url(normalized)

    sitemap_urls = await _discover_sitemaps(gateway, base)
    if not sitemap_urls:
        return {"success": False, "error": "No sitemaps found"}

    if discover_only:
        return {"success": True, "kind": "sitemaps", "urls": sitemap_urls}

    pages = await _expand_sitemaps(gateway, sitemap_urls, lang=lang)
    if limit > 0:
        pages = pages[:limit]

    return {
        "success": True,
        "kind": "pages",
        "count": len(pages),
        "urls": pages,
    }


app = _create_service_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=_HOST, port=_PORT)
