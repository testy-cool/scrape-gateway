from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import StrategyConfig
from .models import ScrapeRequest
from .router import ScrapeGateway

app = typer.Typer(help="Scrape Gateway: cache, route, escalate, remember.")
console = Console(stderr=True)


def _build_gateway(provider: str | None = None) -> ScrapeGateway:
    gateway = ScrapeGateway.from_config()
    if provider:
        gateway.strategy = StrategyConfig(provider=provider)
    return gateway


def _print_result(result) -> None:
    if result.success:
        title = Text("SUCCESS", style="bold green")
    else:
        title = Text("FAILED", style="bold red")

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()

    table.add_row("provider", f"[cyan]{result.provider}[/]")
    table.add_row("route", str(result.route))
    table.add_row("status", str(result.status_code))
    table.add_row("cost", str(result.cost_units))
    if result.content_validated is not None:
        style = "green" if result.content_validated else "red"
        table.add_row("validated", f"[{style}]{result.content_validated}[/]")
    if result.block_type:
        table.add_row("block", f"[red]{result.block_type}[/]")
    if result.failure_reason:
        table.add_row("reason", f"[red]{result.failure_reason}[/]")
    if result.html:
        table.add_row("chars", f"{len(result.html):,}")
    if result.markdown:
        table.add_row("markdown", f"{len(result.markdown):,} chars")

    console.print(Panel(table, title=title, border_style="green" if result.success else "red"))


@app.command()
def url(
    target_url: str,
    country: str | None = typer.Option(None, "--country", "-c"),
    render_js: bool = typer.Option(False, "--render-js"),
    premium: bool = typer.Option(False, "--premium"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="Preferred provider"),
    mobile: bool = typer.Option(False, "--mobile", "-m"),
    wait_event: str | None = typer.Option(None, "--wait-event", help="domcontentloaded|load|networkidle"),
    wait_selector: str | None = typer.Option(None, "--wait-selector", help="CSS selector to wait for"),
    extra_wait: int = typer.Option(0, "--extra-wait", help="Extra wait in ms after page load"),
    block_ads: bool = typer.Option(False, "--block-ads"),
    output_format: str = typer.Option("html", "--format", "-f", help="html|markdown"),
    screenshot: bool = typer.Option(False, "--screenshot"),
) -> None:
    """Scrape one URL through the gateway."""

    async def run() -> None:
        gateway = _build_gateway(provider)
        with console.status(f"[bold cyan]Scraping {target_url}...", spinner="dots"):
            result = await gateway.scrape(
                ScrapeRequest(
                    target_url,
                    country=country,
                    render_js=render_js,
                    premium=premium,
                    screenshot=screenshot,
                    mobile=mobile,
                    wait_event=wait_event,
                    wait_selector=wait_selector,
                    extra_wait_ms=extra_wait,
                    block_ads=block_ads,
                    output_format=output_format,
                ),
                use_cache=not no_cache,
                use_memory=not no_cache,
            )
        _print_result(result)

    asyncio.run(run())


@app.command()
def run(
    urls_file: Path,
    country: str | None = typer.Option(None, "--country", "-c"),
    render_js: bool = typer.Option(False, "--render-js"),
    premium: bool = typer.Option(False, "--premium"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="Preferred provider"),
    mobile: bool = typer.Option(False, "--mobile", "-m"),
    wait_event: str | None = typer.Option(None, "--wait-event", help="domcontentloaded|load|networkidle"),
    wait_selector: str | None = typer.Option(None, "--wait-selector", help="CSS selector to wait for"),
    extra_wait: int = typer.Option(0, "--extra-wait", help="Extra wait in ms after page load"),
    block_ads: bool = typer.Option(False, "--block-ads"),
    output_format: str = typer.Option("html", "--format", "-f", help="html|markdown"),
    screenshot: bool = typer.Option(False, "--screenshot"),
) -> None:
    """Scrape URLs from a text file, one URL per line."""

    async def execute() -> None:
        gateway = _build_gateway(provider)
        urls = [line.strip() for line in urls_file.read_text().splitlines() if line.strip()]
        successes = 0
        total_cost = 0.0

        table = Table(title="Batch Results")
        table.add_column("#", style="dim", width=4)
        table.add_column("URL", max_width=50)
        table.add_column("Provider", style="cyan")
        table.add_column("Status")
        table.add_column("Cost", justify="right")
        table.add_column("Result")

        for i, item in enumerate(urls, 1):
            with console.status(f"[bold cyan][{i}/{len(urls)}] {item}...", spinner="dots"):
                result = await gateway.scrape(
                    ScrapeRequest(
                        item,
                        country=country,
                        render_js=render_js,
                        premium=premium,
                        screenshot=screenshot,
                        mobile=mobile,
                        wait_event=wait_event,
                        wait_selector=wait_selector,
                        extra_wait_ms=extra_wait,
                        block_ads=block_ads,
                        output_format=output_format,
                    )
                )
            successes += int(result.success)
            total_cost += result.cost_units
            status_style = "green" if result.success else "red"
            table.add_row(
                str(i),
                item,
                result.provider,
                str(result.status_code),
                str(result.cost_units),
                f"[{status_style}]{'OK' if result.success else result.block_type or 'FAIL'}[/]",
            )

        console.print(table)
        console.print(
            f"\n[bold]{len(urls)}[/] URLs | "
            f"[green]{successes} OK[/] | "
            f"[red]{len(urls) - successes} failed[/] | "
            f"cost [cyan]{total_cost}[/]"
        )

    asyncio.run(execute())


SEMANTIC_TAGS = {
    "nav": "Navigation",
    "header": "Header",
    "footer": "Footer",
    "aside": "Sidebar",
    "main": "Content",
    "article": "Article",
    "section": "Section",
}


def _extract_links(html: str, base_url: str) -> dict[str, list[tuple[str, str]]]:
    from urllib.parse import urljoin

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    groups: dict[str, list[tuple[str, str]]] = {}

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        href = urljoin(base_url, href)
        text = a.get_text(strip=True)[:80] or href

        group = "Other"
        for parent in a.parents:
            if parent.name in SEMANTIC_TAGS:
                group = SEMANTIC_TAGS[parent.name]
                break

        groups.setdefault(group, []).append((href, text))

    for links in groups.values():
        seen = set()
        deduped = []
        for href, text in links:
            if href not in seen:
                seen.add(href)
                deduped.append((href, text))
        links[:] = deduped

    return groups


@app.command()
def links(
    target_url: str,
    country: str | None = typer.Option(None, "--country", "-c"),
    render_js: bool = typer.Option(False, "--render-js"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="Preferred provider"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Extract and group links from a page by semantic location."""

    async def run() -> None:
        gateway = _build_gateway(provider)
        with console.status(f"[bold cyan]Scraping {target_url}...", spinner="dots"):
            result = await gateway.scrape(
                ScrapeRequest(target_url, country=country, render_js=render_js),
                use_cache=not no_cache,
                use_memory=not no_cache,
            )
        if not result.success or not result.html:
            console.print(f"[red]Scrape failed:[/] {result.error or result.failure_reason}")
            raise typer.Exit(1)

        groups = _extract_links(result.html, result.url)
        total = sum(len(v) for v in groups.values())
        console.print(f"\n[bold]{total}[/] links from [cyan]{result.url}[/]\n")

        order = ["Navigation", "Header", "Content", "Article", "Section", "Sidebar", "Footer", "Other"]
        for group_name in order:
            if group_name not in groups:
                continue
            link_list = groups[group_name]
            table = Table(title=group_name, show_lines=False, title_style="bold")
            table.add_column("Link", style="cyan", max_width=70)
            table.add_column("Text", max_width=50)
            for href, text in link_list:
                table.add_row(href, text)
            console.print(table)
            console.print()

    asyncio.run(run())


import re as _re

DATA_PATTERNS = {
    "Prices": _re.compile(r'(?:[$€£¥₹]\s?\d[\d,. ]*\d|\d[\d,. ]*\d\s?(?:USD|EUR|GBP|RON|lei))', _re.IGNORECASE),
    "Emails": _re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'),
    "Phones": _re.compile(r'(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}'),
    "Dates": _re.compile(r'\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}[/.-]\d{1,2}[/.-]\d{1,2}'),
}


def _detect_patterns(html: str) -> dict:
    from collections import Counter

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    results: dict = {}

    # Repeated elements: find parent elements with 3+ children sharing the same tag+class
    repeated = []
    for parent in soup.find_all(True):
        children = [c for c in parent.children if hasattr(c, "name") and c.name]
        if len(children) < 3:
            continue
        sigs = Counter()
        for child in children:
            cls = " ".join(sorted(child.get("class", [])))
            sig = f"{child.name}.{cls}" if cls else child.name
            sigs[sig] += 1
        for sig, count in sigs.most_common(5):
            if count < 3:
                break
            tag = sig.split(".")[0]
            cls = sig.split(".", 1)[1] if "." in sig else ""
            selector = tag + ("." + ".".join(cls.split()) if cls else "")
            parent_cls = " ".join(parent.get("class", [])[:2])
            parent_sel = parent.name + ("." + ".".join(parent_cls.split()) if parent_cls else "")
            sample_el = parent.find(tag, class_=cls.split() if cls else None)
            sample = sample_el.get_text(strip=True)[:100] if sample_el else ""
            repeated.append({
                "parent": parent_sel, "selector": selector,
                "count": count, "sample": sample,
            })

    # Deduplicate by selector, keep highest count
    seen: dict[str, dict] = {}
    for r in repeated:
        key = f"{r['parent']} > {r['selector']}"
        if key not in seen or r["count"] > seen[key]["count"]:
            seen[key] = r
    results["repeated"] = sorted(seen.values(), key=lambda x: x["count"], reverse=True)[:20]

    # Data patterns from visible text
    text = soup.get_text(" ", strip=True)
    for name, pattern in DATA_PATTERNS.items():
        matches = list(set(pattern.findall(text)))
        if matches:
            results[name.lower()] = matches[:15]

    return results


@app.command()
def detect(
    target_url: str,
    country: str | None = typer.Option(None, "--country", "-c"),
    render_js: bool = typer.Option(False, "--render-js"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="Preferred provider"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Detect repeated elements and data patterns in a page."""

    async def run() -> None:
        gateway = _build_gateway(provider)
        with console.status(f"[bold cyan]Scraping {target_url}...", spinner="dots"):
            result = await gateway.scrape(
                ScrapeRequest(target_url, country=country, render_js=render_js),
                use_cache=not no_cache,
                use_memory=not no_cache,
            )
        if not result.success or not result.html:
            console.print(f"[red]Scrape failed:[/] {result.error or result.failure_reason}")
            raise typer.Exit(1)

        patterns = _detect_patterns(result.html)

        if patterns.get("repeated"):
            table = Table(title="Repeated Elements", title_style="bold")
            table.add_column("Count", justify="right", style="bold cyan", width=6)
            table.add_column("Parent", max_width=30)
            table.add_column("Selector", style="green", max_width=30)
            table.add_column("Sample", max_width=50, style="dim")
            for r in patterns["repeated"]:
                table.add_row(str(r["count"]), r["parent"], r["selector"], r["sample"])
            console.print(table)
            console.print()

        for name, label in [("prices", "Prices"), ("emails", "Emails"), ("phones", "Phones"), ("dates", "Dates")]:
            items = patterns.get(name, [])
            if items:
                console.print(f"[bold]{label}[/] ({len(items)} found)")
                for item in items[:10]:
                    console.print(f"  [dim]•[/] {item}")
                console.print()

        if not patterns.get("repeated") and not any(patterns.get(k) for k in ("prices", "emails", "phones", "dates")):
            console.print("[dim]No patterns detected.[/]")

    asyncio.run(run())


@app.command()
def selftest() -> None:
    """Run a live smoke test against safe public URLs."""

    tests = [
        ("https://example.com", "clean static page"),
        ("https://httpbin.org/html", "real HTML content"),
        ("https://httpbin.org/status/403", "HTTP 403 rejection"),
    ]

    async def run_tests() -> None:
        import tempfile

        tmp = tempfile.mkdtemp()
        from .cache import ArtifactCache
        from .memory import DomainMemory
        from .providers.raw_http import RawHttpProvider

        gateway = ScrapeGateway(
            providers=[RawHttpProvider()],
            cache=ArtifactCache(root=Path(tmp) / "cache"),
            memory=DomainMemory(db_path=Path(tmp) / "mem.sqlite"),
        )

        passed = 0
        failed = 0
        for target_url, description in tests:
            with console.status(f"[bold cyan]{description}...", spinner="dots"):
                result = await gateway.scrape(ScrapeRequest(target_url), use_cache=False)
            ok = (result.success and result.content_validated) or (
                not result.success and result.failure_reason is not None
            )
            if ok:
                passed += 1
                console.print(f"  [green]PASS[/]  {description}")
            else:
                failed += 1
                console.print(f"  [red]FAIL[/]  {description}")

        console.print(f"\n[green]{passed} passed[/], [red]{failed} failed[/]")
        raise typer.Exit(code=1 if failed else 0)

    asyncio.run(run_tests())
