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


def _hints(cmd: str, url: str = "", **ctx) -> None:
    console.print("\n[dim]---[/]")
    url_display = url or "<url>"
    if cmd == "url":
        console.print(f"[dim]sg links {url_display}          # extract & index all links[/]")
        console.print(f"[dim]sg detect {url_display}         # find repeated elements & data patterns[/]")
        console.print(f"[dim]sg history {url_display}        # view change history[/]")
        console.print(f"[dim]sg url {url_display} --render-js  # re-scrape with JS rendering[/]")
    elif cmd == "links":
        fmt = ctx.get("fmt", "rich")
        if fmt != "compact":
            console.print(f"[dim]sg links {url_display} -f compact  # LLM-optimized tree output[/]")
        if fmt != "json":
            console.print(f"[dim]sg links {url_display} -f json     # pipe to jq[/]")
        console.print(f"[dim]sg follow {url_display} <id>        # scrape a link by index[/]")
        console.print(f"[dim]sg detect {url_display}             # find repeated elements[/]")
    elif cmd == "follow":
        followed = ctx.get("followed_url", url_display)
        console.print(f"[dim]sg links {followed}          # extract links from followed page[/]")
        console.print(f"[dim]sg detect {followed}         # find patterns in followed page[/]")
        console.print(f"[dim]sg history {followed}        # view change history[/]")
    elif cmd == "detect":
        console.print(f"[dim]sg extract {url_display}            # pull data from top pattern[/]")
        console.print(f"[dim]sg extract {url_display} -s 'sel'   # extract with custom selector[/]")
        console.print(f"[dim]sg links {url_display}              # see all links indexed[/]")
        console.print(f"[dim]sg history {url_display}            # track changes over time[/]")
    elif cmd == "extract":
        console.print(f"[dim]sg detect {url_display}             # see all detected patterns[/]")
        console.print(f"[dim]sg extract {url_display} -f csv     # CSV output[/]")
        console.print(f"[dim]sg extract {url_display} -s 'sel'   # custom CSS selector[/]")
        console.print(f"[dim]sg links {url_display}              # see all links indexed[/]")
    elif cmd == "history":
        console.print(f"[dim]sg url {url_display} --no-cache     # fresh scrape to update history[/]")
        console.print(f"[dim]sg detect {url_display}             # analyze current structure[/]")
        console.print(f"[dim]sg links {url_display} -f compact   # LLM-optimized link tree[/]")
    elif cmd == "selftest":
        console.print(f"[dim]sg url <url>                   # scrape any URL[/]")
        console.print(f"[dim]sg links <url> -f compact      # extract links for LLM[/]")


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
        _hints("url", target_url)

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


def _compact_links(all_links: list[dict], groups: dict[str, list[int]], base_url: str, limit: int = 0) -> str:
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
                show = items if not limit else items[:limit]
                remaining = len(items) - len(show)
                lines.append(f"{prefix} ({len(items)})" if limit else prefix)
                for idx, path, text in show:
                    suffix = path[len(prefix):]
                    lines.append(f"  [{idx}] {suffix or '.'} {text}")
                if remaining > 0:
                    lines.append(f"  ... +{remaining} more")

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
    limit: int = typer.Option(0, "--limit", "-n", help="Max links per directory in compact mode (0=all)"),
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
            print(_compact_links(all_links, groups, result.url, limit=limit))
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
            _hints("links", target_url, fmt=output_format)

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
        _hints("follow", target_url, followed_url=match["href"])

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
        _hints("detect", target_url)

    asyncio.run(run())


def _element_to_row(el) -> dict[str, str]:
    row: dict[str, str] = {}
    el_text = el.get_text(" ", strip=True)

    heading = el.find(["h1", "h2", "h3", "h4", "h5", "h6"])
    if heading:
        row["title"] = heading.get_text(strip=True)
        a = heading.find("a")
        if a and a.get("href"):
            row["href"] = a["href"]
        elif a := heading.find_parent("a"):
            if a.get("href"):
                row["href"] = a["href"]

    img = el.find("img")
    if img and img.get("src"):
        row["image"] = img["src"]
        alt = img.get("alt", "")
        if alt and alt != row.get("title", ""):
            row["image_alt"] = alt

    price_match = DATA_PATTERNS["Prices"].search(el_text)
    if price_match:
        row["price"] = price_match.group()

    if "href" not in row:
        a = el.find("a", href=True)
        if a:
            row.setdefault("title", a.get_text(strip=True))
            row["href"] = a["href"]

    time_el = el.find("time")
    if time_el:
        row["date"] = time_el.get("datetime", time_el.get_text(strip=True))
    elif date_match := DATA_PATTERNS["Dates"].search(el_text):
        row["date"] = date_match.group()

    skip_tags = {"script", "style", "img", "a", "h1", "h2", "h3",
                  "h4", "h5", "h6", "time", "br", "hr", "i", "svg",
                  "path", "button", "input", "form", "select", "noscript"}
    captured = set(row.values())
    for child in el.find_all(True, recursive=True):
        if child.name in skip_tags:
            continue
        cls = child.get("class", [])
        if not cls:
            continue
        t = child.get_text(strip=True)
        if not t or len(t) < 2 or t in captured:
            continue
        sub_children = [c for c in child.children if hasattr(c, "name") and c.name]
        if len(sub_children) > 2:
            continue
        name = cls[0]
        if name not in row:
            row[name] = t
            captured.add(t)

    return row


def _extract_rows(html: str, selector: str | None = None, pick: int = 1) -> tuple[list[dict], str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    if selector:
        elements = soup.select(selector)
        desc = f"{selector} ({len(elements)} items)"
    else:
        patterns = _detect_patterns(html)
        repeated = patterns.get("repeated", [])
        if not repeated:
            return [], "no repeated patterns found"
        idx = max(0, min(pick - 1, len(repeated) - 1))
        top = repeated[idx]
        css = f"{top['parent']} > {top['selector']}"
        elements = soup.select(css)
        if not elements:
            elements = soup.select(top["selector"])
        desc = f"{css} ({len(elements)} items)"

    rows = [r for el in elements if (r := _element_to_row(el))]
    return rows, desc


def _apply_field_map(rows: list[dict], field_map: dict[str, str]) -> list[dict]:
    if not field_map:
        return rows
    return [{field_map.get(k, k): v for k, v in row.items()} for row in rows]


def _llm_pick_pattern(
    patterns: list[dict], html: str, domain: str,
    model_id: str | None = None,
) -> tuple[str, dict[str, str]] | None:
    import json

    import llm

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    lines = []
    for i, p in enumerate(patterns[:8], 1):
        css = f"{p['parent']} > {p['selector']}"
        elements = soup.select(css) or soup.select(p["selector"])
        fields: list[str] = []
        if elements:
            row = _element_to_row(elements[0])
            fields = list(row.keys())
        field_str = f"  fields: {', '.join(fields)}" if fields else ""
        lines.append(
            f"#{i}: {css} ({p['count']} items) — \"{p['sample'][:60]}\"\n{field_str}"
        )

    prompt = (
        f"Domain: {domain}\n\nDetected patterns:\n"
        + "\n".join(lines)
        + '\n\nPick the pattern that is the main content listing '
        '(products, articles, results), not navigation or boilerplate.\n'
        'Reply JSON: {"pick": <number>, "fields": {"<raw>": "<better>", ...}}\n'
        'Only rename unclear field names. Keep title, price, image, href, date as-is.'
    )

    try:
        model = llm.get_model(model_id) if model_id else llm.get_model()
        console.print(f"[dim]llm: using {model.model_id}[/]")
        response = model.prompt(prompt, stream=False)
        text = response.text()
        text = text.strip().removeprefix("```json").removesuffix("```").strip()
        result = json.loads(text)
        pick_idx = max(0, min(int(result["pick"]) - 1, len(patterns) - 1))
        p = patterns[pick_idx]
        selector = f"{p['parent']} > {p['selector']}"
        field_map = result.get("fields", {})
        return selector, field_map
    except llm.UnknownModelError as exc:
        console.print(f"[red]llm error:[/] unknown model: {model_id}\n  available: llm models list")
        return None
    except llm.NeedsKeyException as exc:
        console.print(f"[red]llm error:[/] no API key for {model_id or 'default model'}\n  run: llm keys set <name>")
        return None
    except json.JSONDecodeError as exc:
        console.print(f"[dim]llm: model returned invalid JSON: {exc}[/]")
        return None
    except Exception as exc:
        console.print(f"[dim]llm: {type(exc).__name__}: {exc}[/]")
        return None


@app.command()
def extract(
    target_url: str,
    selector: str | None = typer.Option(None, "--selector", "-s", help="CSS selector (auto-detect if omitted)"),
    pick: int = typer.Option(1, "--pick", "-k", help="Which detected pattern to use (1=top, 2=second, ...)"),
    country: str | None = typer.Option(None, "--country", "-c"),
    render_js: bool = typer.Option(False, "--render-js"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="Preferred provider"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip LLM pattern picking"),
    model: str | None = typer.Option(None, "--model", "-m", help="LLM model for pattern picking (default: llm default)"),
    output_format: str = typer.Option("json", "--format", "-f", help="json|csv|rich"),
    limit: int = typer.Option(0, "--limit", "-n", help="Max rows (0=all)"),
) -> None:
    """Extract structured data from repeated page elements."""

    async def run() -> None:
        from .config import load_config
        from .memory import DomainMemory

        gateway = _build_gateway(provider)
        with console.status(f"[bold cyan]Scraping {target_url}...", spinner="dots"):
            result = await gateway.scrape(
                ScrapeRequest(target_url, country=country, render_js=render_js),
                use_cache=not no_cache,
                use_memory=not no_cache,
            )
        if not result.success:
            reason = result.failure_reason or "unknown"
            detail = result.error or ""
            console.print(
                f"[red]error:[/] scrape failed for {target_url}\n"
                f"  provider: {result.provider or 'none'}\n"
                f"  status: {result.status_code}\n"
                f"  reason: {reason}\n"
                + (f"  detail: {detail}\n" if detail else "")
                + "  hint: try --render-js, --provider scrapedrive, or --no-cache"
            )
            raise typer.Exit(1)
        if not result.html:
            console.print(
                f"[red]error:[/] scrape returned empty HTML for {target_url}\n"
                f"  provider: {result.provider}\n"
                f"  hint: the page may require JS rendering (--render-js)"
            )
            raise typer.Exit(1)

        config = load_config()
        memory = DomainMemory(db_path=config.memory_path)
        domain = DomainMemory.domain_for_url(result.url)
        field_map: dict[str, str] = {}

        if selector:
            rows, desc = _extract_rows(result.html, selector)
            if not rows:
                console.print(
                    f"[red]error:[/] selector matched 0 elements: {selector}\n"
                    f"  hint: run 'sg detect {target_url}' to see available patterns"
                )
                raise typer.Exit(1)
        elif not no_llm:
            cached = memory.get_extraction(domain)
            if cached:
                sel, field_map = cached
                rows, desc = _extract_rows(result.html, sel)
                if not rows:
                    memory.learn_extraction(domain, "", {})
                    console.print(
                        f"[red]error:[/] learned selector no longer matches: {sel}\n"
                        f"  cleared stale pattern for {domain}\n"
                        f"  hint: re-run to re-learn, or use --selector"
                    )
                    raise typer.Exit(1)
                desc = f"{desc} (learned)"
            else:
                patterns = _detect_patterns(result.html)
                repeated = patterns.get("repeated", [])
                if not repeated:
                    console.print(
                        f"[red]error:[/] no repeated elements found on {target_url}\n"
                        f"  page has {len(result.html):,} chars of HTML\n"
                        f"  hint: page may need JS rendering (--render-js) or has no listing structure"
                    )
                    raise typer.Exit(1)

                with console.status("[bold cyan]Asking LLM to pick pattern...", spinner="dots"):
                    llm_result = _llm_pick_pattern(repeated, result.html, domain, model)

                if llm_result:
                    sel, field_map = llm_result
                    memory.learn_extraction(domain, sel, field_map)
                    rows, desc = _extract_rows(result.html, sel)
                    desc = f"{desc} (llm)"
                else:
                    rows, desc = _extract_rows(result.html, pick=pick)
                    if rows:
                        desc = f"{desc} (heuristic — llm fallback)"
        else:
            rows, desc = _extract_rows(result.html, pick=pick)

        if not rows:
            console.print(
                f"[red]error:[/] extraction returned 0 rows from {target_url}\n"
                f"  pattern: {desc}\n"
                f"  hint: try 'sg detect {target_url}' and use --selector or --pick N"
            )
            raise typer.Exit(1)

        rows = _apply_field_map(rows, field_map)
        if limit:
            rows = rows[:limit]

        console.print(f"[dim]{desc}[/]\n")

        if output_format == "csv":
            import csv
            import io

            all_keys = list(dict.fromkeys(k for r in rows for k in r))
            out = io.StringIO()
            writer = csv.DictWriter(out, fieldnames=all_keys)
            writer.writeheader()
            writer.writerows(rows)
            print(out.getvalue(), end="")
        elif output_format == "rich":
            table = Table(title=f"Extracted ({len(rows)} rows)", show_lines=True)
            all_keys = list(dict.fromkeys(k for r in rows for k in r))
            for k in all_keys:
                table.add_column(k, max_width=40)
            for r in rows:
                table.add_row(*(str(r.get(k, "")) for k in all_keys))
            console.print(table)
        else:
            import json
            print(json.dumps(rows, indent=2, ensure_ascii=False))

        _hints("extract", target_url)

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

    _hints("history", target_url)
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
        _hints("selftest")
        raise typer.Exit(code=1 if failed else 0)

    asyncio.run(run_tests())
