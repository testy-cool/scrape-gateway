from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urlparse
from xml.etree import ElementTree

import typer
from rich.console import Console
from rich.table import Table

console = Console(stderr=True)

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
COMMON_SITEMAP_PATHS = ["/sitemap.xml", "/sitemap_index.xml", "/sitemap/sitemap.xml"]


def _normalize_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return f"https://{url}"
    return url


def _base_url(url: str) -> str:
    parsed = urlparse(_normalize_url(url))
    if not parsed.netloc:
        raise ValueError(f"Invalid URL: {url}")
    return f"{parsed.scheme}://{parsed.netloc}"


def _parse_robots_sitemaps(text: str) -> list[str]:
    urls = []
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("sitemap:"):
            url = line.split(":", 1)[1].strip()
            if url:
                urls.append(url)
    return urls


def _parse_sitemap_xml(xml_text: str) -> tuple[list[str], list[str]]:
    """Parse a sitemap XML. Returns (page_urls, child_sitemap_urls)."""
    pages: list[str] = []
    children: list[str] = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return pages, children

    tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag

    if tag == "sitemapindex":
        for sitemap in root.findall("sm:sitemap/sm:loc", SITEMAP_NS):
            if sitemap.text:
                children.append(sitemap.text.strip())
        if not children:
            for sitemap in root.findall(".//loc"):
                if sitemap.text:
                    children.append(sitemap.text.strip())
    elif tag == "urlset":
        for url_el in root.findall("sm:url/sm:loc", SITEMAP_NS):
            if url_el.text:
                pages.append(url_el.text.strip())
        if not pages:
            for loc in root.findall(".//loc"):
                if loc.text:
                    pages.append(loc.text.strip())
    else:
        for loc in root.findall(".//loc"):
            if loc.text:
                pages.append(loc.text.strip())

    return pages, children


async def _fetch(gateway, url: str) -> str | None:
    from scrape_gateway.models import ScrapeRequest

    result = await gateway.scrape(
        ScrapeRequest(url, output_format="html", skip_validation=True),
        use_cache=True,
        use_memory=False,
    )
    if result.success and result.html:
        return result.html
    return None


async def _discover_sitemaps(gateway, base: str) -> list[str]:
    robots_url = f"{base}/robots.txt"
    text = await _fetch(gateway, robots_url)
    if text:
        found = _parse_robots_sitemaps(text)
        if found:
            return found

    for path in COMMON_SITEMAP_PATHS:
        url = f"{base}{path}"
        text = await _fetch(gateway, url)
        if text and "<" in text:
            return [url]

    return []


async def _expand_sitemaps(
    gateway,
    sitemap_urls: list[str],
    *,
    max_sitemaps: int = 10000,
    lang: str | None = None,
) -> list[str]:
    all_pages: list[str] = []
    seen: set[str] = set()
    queue = list(sitemap_urls)
    processed = 0

    while queue and processed < max_sitemaps:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        processed += 1

        text = await _fetch(gateway, url)
        if not text:
            continue

        pages, children = _parse_sitemap_xml(text)
        all_pages.extend(pages)
        queue.extend(children)

    if lang:
        lang_lower = lang.lower()
        filtered = [u for u in all_pages if f"/{lang_lower}/" in u.lower() or f"lang={lang_lower}" in u.lower()]
        if filtered:
            return filtered

    return all_pages


def _print_urls_json(target_url: str, urls: list[str], *, kind: str) -> None:
    print(
        json.dumps(
            {
                "url": _normalize_url(target_url),
                "kind": kind,
                "count": len(urls),
                "urls": urls,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def _print_urls_rich(urls: list[str], *, title: str, limit: int = 0) -> None:
    table = Table(title=title, show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("URL", overflow="fold")
    shown = urls[:limit] if limit else urls
    for idx, url in enumerate(shown, start=1):
        table.add_row(str(idx), url)
    console.print(table)
    if limit and len(urls) > limit:
        console.print(f"[dim]Showing {limit} of {len(urls)} URLs[/]")


def register(app: typer.Typer) -> None:
    @app.command("sitemap")
    def sitemap(
        target_url: str = typer.Argument(..., help="Site, page, or sitemap URL"),
        output_format: str = typer.Option("json", "--format", "-f", help="json|txt|rich"),
        limit: int = typer.Option(0, "--limit", "-n", help="Max page URLs to print (0=all)"),
        lang: str | None = typer.Option(
            None, "--lang", "-l", help="Filter page URLs by ISO 639-1 language code"
        ),
        max_sitemaps: int = typer.Option(
            10000, "--max-sitemaps", help="Maximum sitemap files to process"
        ),
        discover_only: bool = typer.Option(
            False,
            "--discover-only",
            help="Only print sitemap URLs declared in robots.txt; do not expand page URLs",
        ),
        provider: str | None = typer.Option(
            None, "--provider", "-p", help="Preferred provider"
        ),
        no_cache: bool = typer.Option(False, "--no-cache"),
    ) -> None:
        """Find and expand sitemaps via the scrape gateway.

        Fetches robots.txt and sitemap XML files through sgw's provider
        pipeline, so anti-bot bypass, proxies, and provider fallback all
        apply.

        By default this prints page URLs extracted from sitemap files. Use
        --discover-only when you only want sitemap XML URLs advertised in
        robots.txt.
        """
        fmt = output_format.lower()
        if fmt not in {"json", "txt", "rich"}:
            console.print("[red]Unsupported format. Use json, txt, or rich.[/]")
            raise typer.Exit(1)

        async def run() -> None:
            from scrape_gateway.config import StrategyConfig
            from scrape_gateway.router import ScrapeGateway

            gateway = ScrapeGateway.from_config()
            if provider:
                gateway.strategy = StrategyConfig(provider=provider)

            normalized = _normalize_url(target_url)
            base = _base_url(normalized)

            with console.status("[bold cyan]Discovering sitemaps...", spinner="dots"):
                sitemap_urls = await _discover_sitemaps(gateway, base)

            if not sitemap_urls:
                console.print("[yellow]No sitemaps found.[/]")
                raise typer.Exit(1)

            if discover_only:
                urls = sitemap_urls
                kind = "sitemaps"
                title = f"Sitemaps declared in robots.txt for {base}"
            else:
                with console.status(
                    f"[bold cyan]Expanding {len(sitemap_urls)} sitemap(s)...", spinner="dots"
                ):
                    urls = await _expand_sitemaps(
                        gateway,
                        sitemap_urls,
                        max_sitemaps=max_sitemaps,
                        lang=lang,
                    )
                kind = "pages"
                title = f"Sitemap page URLs for {normalized}"

            if not urls:
                console.print("[yellow]No sitemap URLs found.[/]")
                raise typer.Exit(1)

            output_urls = urls[:limit] if limit and not discover_only else urls

            if fmt == "json":
                _print_urls_json(target_url, output_urls, kind=kind)
            elif fmt == "txt":
                for url in output_urls:
                    print(url)
            else:
                _print_urls_rich(urls, title=title, limit=limit if not discover_only else 0)

        asyncio.run(run())
