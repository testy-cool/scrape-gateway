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


def _extract_links(html: str, base_url: str) -> tuple[list[dict], dict[str, list[int]]]:
    """Returns (flat indexed list, section->indices mapping)."""
    from urllib.parse import urljoin

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    all_links: list[dict] = []
    groups: dict[str, list[int]] = {}
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        href = urljoin(base_url, href)
        if href in seen:
            continue
        seen.add(href)
        text = a.get_text(strip=True)[:80] or href

        section = "other"
        for parent in a.parents:
            if parent.name in SEMANTIC_TAGS:
                section = parent.name
                break

        idx = len(all_links) + 1
        all_links.append({"id": idx, "href": href, "text": text, "section": section})
        groups.setdefault(section, []).append(idx)

    return all_links, groups


def _to_path(href: str, origins: set[str]) -> tuple[str, bool]:
    for o in origins:
        if href.startswith(o):
            return (href[len(o):] or "/", True)
    if href.startswith("/"):
        return (href, True)
    return (href, False)


def _compact_links(all_links: list[dict], groups: dict[str, list[int]], base_url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    origins = {f"{parsed.scheme}://{host}"}
    if host.startswith("www."):
        origins.add(f"{parsed.scheme}://{host[4:]}")
    else:
        origins.add(f"{parsed.scheme}://www.{host}")

    by_id = {link["id"]: link for link in all_links}
    lines = [f"# {host} ({len(all_links)} links)\n"]

    section_order = ["nav", "header", "main", "article", "section", "aside", "footer", "other"]

    for section in section_order:
        if section not in groups:
            continue
        indices = groups[section]
        lines.append(f"## {section} ({len(indices)})")

        internal: list[tuple[int, str, str]] = []
        external: list[tuple[int, str, str]] = []
        for idx in indices:
            link = by_id[idx]
            path, is_internal = _to_path(link["href"], origins)
            if is_internal:
                internal.append((idx, path, link["text"]))
            else:
                external.append((idx, link["href"], link["text"]))

        prefix_groups: dict[str, list[tuple[int, str, str]]] = {}
        for idx, path, text in internal:
            parts = path.strip("/").split("/")
            prefix = "/" + parts[0] + "/" if len(parts) >= 2 else ""
            prefix_groups.setdefault(prefix, []).append((idx, path, text))

        for prefix, items in prefix_groups.items():
            if not prefix or len(items) == 1:
                for idx, path, text in items:
                    lines.append(f"[{idx}] {path} {text}")
            else:
                lines.append(prefix)
                for idx, path, text in items:
                    suffix = path[len(prefix):]
                    lines.append(f"  [{idx}] {suffix or '.'} {text}")

        for idx, href, text in external:
            lines.append(f"[{idx}] {href} {text}")
        lines.append("")

    return "\n".join(lines)


@app.command()
def links(
    target_url: str,
    country: str | None = typer.Option(None, "--country", "-c"),
    render_js: bool = typer.Option(False, "--render-js"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="Preferred provider"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    output_format: str = typer.Option("rich", "--format", "-f", help="rich|compact|json"),
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

        all_links, groups = _extract_links(result.html, result.url)

        if output_format == "json":
            import json
            print(json.dumps(all_links, indent=2, ensure_ascii=False))
        elif output_format == "compact":
            print(_compact_links(all_links, groups, result.url))
        else:
            console.print(f"\n[bold]{len(all_links)}[/] links from [cyan]{result.url}[/]\n")
            section_order = ["nav", "header", "main", "article", "section", "aside", "footer", "other"]
            by_id = {link["id"]: link for link in all_links}
            for section in section_order:
                if section not in groups:
                    continue
                label = SEMANTIC_TAGS.get(section, section.title())
                indices = groups[section]
                table = Table(title=label, show_lines=False, title_style="bold")
                table.add_column("#", style="dim", width=5)
                table.add_column("Link", style="cyan", max_width=65)
                table.add_column("Text", max_width=50)
                for idx in indices:
                    link = by_id[idx]
                    table.add_row(str(idx), link["href"], link["text"])
                console.print(table)
                console.print()

    asyncio.run(run())


@app.command()
def follow(
    target_url: str,
    link_id: int = typer.Argument(..., help="Link index from sg links output"),
    country: str | None = typer.Option(None, "--country", "-c"),
    render_js: bool = typer.Option(False, "--render-js"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="Preferred provider"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Scrape a page, pick a link by index, then scrape that link."""

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

        all_links, _ = _extract_links(result.html, result.url)
        match = next((l for l in all_links if l["id"] == link_id), None)
        if not match:
            console.print(f"[red]Link [{link_id}] not found[/] (max: {len(all_links)})")
            raise typer.Exit(1)

        console.print(f"[dim]Following [{link_id}] {match['text']}[/] → [cyan]{match['href']}[/]\n")
        with console.status(f"[bold cyan]Scraping {match['href']}...", spinner="dots"):
            follow_result = await gateway.scrape(
                ScrapeRequest(match["href"], country=country, render_js=render_js),
                use_cache=not no_cache,
                use_memory=not no_cache,
            )
        _print_result(follow_result)

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
def history(
    target_url: str,
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """Show scrape history and structural changes for a URL."""
    from .config import load_config
    from .memory import DomainMemory

    config = load_config()
    memory = DomainMemory(db_path=config.memory_path)

    if not target_url.startswith(("http://", "https://")):
        target_url = f"https://{target_url}"

    entries = memory.get_history(target_url, limit=limit)
    if not entries:
        console.print(f"[dim]No history for {target_url}[/]")
        raise typer.Exit(0)

    console.print(f"\n[bold]{len(entries)}[/] scrapes of [cyan]{target_url}[/]\n")

    table = Table(show_lines=True)
    table.add_column("When", style="dim", width=19)
    table.add_column("Provider", style="cyan", width=12)
    table.add_column("Hash", width=8)
    table.add_column("Links", justify="right", width=6)
    table.add_column("Chars", justify="right", width=8)
    table.add_column("Changes")

    for entry in entries:
        fp = entry["fingerprint"]
        changes = entry["changes"]
        change_text = "; ".join(changes) if changes else "[dim]first scrape[/]"
        if changes == ["no changes"]:
            change_text = "[dim]no changes[/]"
        table.add_row(
            entry["scraped_at"][:19],
            entry["provider"] or "?",
            entry["content_hash"][:8],
            str(fp.get("link_count", "")),
            f"{fp.get('text_length', 0):,}",
            change_text,
        )

    console.print(table)

    if entries:
        latest = entries[0]["fingerprint"]
        console.print(f"\n[bold]Latest fingerprint:[/]")
        console.print(f"  title: [cyan]{latest.get('title', '')}[/]")
        console.print(f"  links: {latest.get('link_count', 0)}  images: {latest.get('image_count', 0)}  "
                       f"forms: {latest.get('form_count', 0)}  prices: {latest.get('price_count', 0)}")
        heads = latest.get("headings", [])
        if heads:
            console.print(f"  headings: {', '.join(heads[:5])}")

    raise typer.Exit(0)


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
