from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import StrategyConfig
from .discovery import load_command_extensions
from .models import ScrapeRequest
from .router import ScrapeGateway

app = typer.Typer(
    help="""Scrape Gateway — a CLI that scrapes web pages through multiple providers,
picks the cheapest one that works, and remembers what worked per domain.

Core idea: you shouldn't have to think about which scraping API to use,
or retry manually when one gets blocked. sgw handles provider fallback,
content validation (catches Cloudflare/captcha pages), tier escalation,
and domain memory automatically.

Commands:
  url       Scrape a single page
  run       Batch scrape from a file of URLs
  links     Find and index all links on a page
  follow    Jump to a link by its index number
  detect    Find repeated elements (product cards, article lists, etc.)
  extract   Pull structured data (JSON/CSV) from those repeated elements
  recipe    Replay a saved scrape+extract workflow from a YAML file
  history   See how a page changed across scrapes
  evaluations  Aggregate AI quality audits and review recurring failures
  selftest  Verify the tool works against safe public URLs"""
)
console = Console(stderr=True)


def _build_gateway(provider: str | None = None) -> ScrapeGateway:
    gateway = ScrapeGateway.from_config()
    if provider:
        gateway.strategy = StrategyConfig(provider=provider)
    return gateway


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", help="Address to bind"),
    port: int = typer.Option(8100, "--port", min=1, max=65535, help="Port to bind"),
    token: str | None = typer.Option(
        None,
        "--token",
        envvar="SGW_SERVICE_TOKEN",
        help="Optional bearer token (or set SGW_SERVICE_TOKEN)",
    ),
) -> None:
    """Expose the gateway through its FastAPI HTTP service."""

    import os

    try:
        import uvicorn

        from .service import create_app
    except ImportError as exc:
        console.print(
            '[red]Service dependencies are missing. Install with `pip install -e ".[server]"`.[/]'
        )
        raise typer.Exit(1) from exc

    service = create_app(token=token or os.environ.get("SGW_MCP_TOKEN", ""))
    uvicorn.run(service, host=host, port=port)


def _hints(cmd: str, url: str = "", **ctx) -> None:
    console.print("\n[dim]---[/]")
    url_display = url or "<url>"
    if cmd == "url":
        console.print(f"[dim]sgw links {url_display}          # extract & index all links[/]")
        console.print(
            f"[dim]sgw detect {url_display}         # find repeated elements & data patterns[/]"
        )
        console.print(f"[dim]sgw history {url_display}        # view change history[/]")
        console.print(f"[dim]sgw url {url_display} --render-js  # re-scrape with JS rendering[/]")
    elif cmd == "links":
        fmt = ctx.get("fmt", "rich")
        if fmt != "compact":
            console.print(
                f"[dim]sgw links {url_display} -f compact  # LLM-optimized tree output[/]"
            )
        if fmt != "json":
            console.print(f"[dim]sgw links {url_display} -f json     # pipe to jq[/]")
        console.print(f"[dim]sgw follow {url_display} <id>        # scrape a link by index[/]")
        console.print(f"[dim]sgw detect {url_display}             # find repeated elements[/]")
    elif cmd == "follow":
        followed = ctx.get("followed_url", url_display)
        console.print(f"[dim]sgw links {followed}          # extract links from followed page[/]")
        console.print(f"[dim]sgw detect {followed}         # find patterns in followed page[/]")
        console.print(f"[dim]sgw history {followed}        # view change history[/]")
    elif cmd == "detect":
        console.print(f"[dim]sgw extract {url_display}            # pull data from top pattern[/]")
        console.print(
            f"[dim]sgw extract {url_display} -s 'sel'   # extract with custom selector[/]"
        )
        console.print(f"[dim]sgw links {url_display}              # see all links indexed[/]")
        console.print(f"[dim]sgw history {url_display}            # track changes over time[/]")
    elif cmd == "extract":
        console.print(f"[dim]sgw detect {url_display}             # see all detected patterns[/]")
        console.print(f"[dim]sgw extract {url_display} -f csv     # CSV output[/]")
        console.print(f"[dim]sgw extract {url_display} -s 'sel'   # custom CSS selector[/]")
        console.print(f"[dim]sgw links {url_display}              # see all links indexed[/]")
    elif cmd == "history":
        console.print(
            f"[dim]sgw url {url_display} --no-cache     # fresh scrape to update history[/]"
        )
        console.print(f"[dim]sgw detect {url_display}             # analyze current structure[/]")
        console.print(f"[dim]sgw links {url_display} -f compact   # LLM-optimized link tree[/]")
    elif cmd == "search":
        q = ctx.get("query", "<query>")
        console.print(f'[dim]sgw search "{q}" --proxy        # route via residential proxy[/]')
        console.print(f'[dim]sgw search "{q}" -f urls        # just URLs, pipe to sgw run[/]')
        console.print(f'[dim]sgw search "{q}" -t w           # last week only[/]')
    elif cmd == "selftest":
        console.print("[dim]sgw url <url>                   # scrape any URL[/]")
        console.print("[dim]sgw links <url> -f compact      # extract links for LLM[/]")


def _extract_page_metadata(html: str, base_url: str | None = None) -> dict[str, object]:
    import re
    from urllib.parse import urljoin

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    metadata: dict[str, object] = {}
    for tag in soup.find_all("meta"):
        key = str(tag.get("property", "") or tag.get("name", "")).strip().lower()
        content = str(tag.get("content", "")).strip()
        if key.startswith(("og:", "twitter:")) and content:
            metadata[key] = content
        if key == "robots" and content:
            metadata["robots"] = content

        charset = str(tag.get("charset", "")).strip()
        if charset and "charset" not in metadata:
            metadata["charset"] = charset
        if (
            str(tag.get("http-equiv", "")).strip().lower() == "content-type"
            and content
            and "charset" not in metadata
        ):
            match = re.search(r"charset\s*=\s*['\"]?([^\s;'\">]+)", content, re.IGNORECASE)
            if match:
                metadata["charset"] = match.group(1)

    if not metadata.get("og:title") and soup.title and soup.title.string:
        metadata["og:title"] = soup.title.string.strip()
    if not metadata.get("og:image"):
        best_src, best_area = "", 0
        for img in soup.find_all("img", src=True):
            src = img["src"]
            if not src.startswith("http") or "rsrc.php" in src or "emoji" in src:
                continue
            try:
                w = int(img.get("width", 0))
                h = int(img.get("height", 0))
            except (ValueError, TypeError):
                w, h = 0, 0
            area = w * h
            if area > best_area:
                best_src, best_area = src, area
            elif area == 0 and not best_area and not best_src:
                best_src = src
        if best_src:
            metadata["og:image"] = re.sub(r"&amp;", "&", best_src)

    json_ld: list[object] = []
    for script in soup.find_all("script"):
        script_type = str(script.get("type", "")).split(";", 1)[0].strip().lower()
        if script_type != "application/ld+json":
            continue
        payload = script.string or script.get_text()
        if not payload or not payload.strip():
            continue
        try:
            json_ld.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    if json_ld:
        metadata["json_ld"] = json_ld

    for link in soup.find_all("link", href=True):
        rel = link.get("rel", [])
        rel_tokens = {part.lower() for part in (rel if isinstance(rel, list) else rel.split())}
        href = str(link["href"]).strip()
        if not href:
            continue
        resolved_href = urljoin(base_url, href) if base_url else href
        if "canonical" in rel_tokens and "canonical" not in metadata:
            metadata["canonical"] = resolved_href
        if "apple-touch-icon" in rel_tokens and "apple_touch_icon" not in metadata:
            metadata["apple_touch_icon"] = resolved_href
        elif "icon" in rel_tokens and "favicon" not in metadata:
            metadata["favicon"] = resolved_href

    return metadata


def _extract_og_meta(html: str) -> dict[str, str]:
    return {
        key: value
        for key, value in _extract_page_metadata(html).items()
        if key.startswith("og:") and isinstance(value, str)
    }


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
        table.add_row("reason", f"[red]{result.failure_reason.value}[/]")
    if result.error:
        table.add_row("error", f"[red]{result.error}[/]")
    if result.html:
        table.add_row("chars", f"{len(result.html):,}")
    if result.markdown:
        table.add_row("markdown", f"{len(result.markdown):,} chars")
    if result.metadata.get("run_id"):
        table.add_row("run", f"[dim]{result.metadata['run_id']}[/]")
    if result.metadata.get("telemetry_report"):
        table.add_row("report", f"[dim]{result.metadata['telemetry_report']}[/]")
    evaluation = result.metadata.get("evaluation")
    if isinstance(evaluation, dict):
        audit_result = evaluation.get("verdict") or evaluation.get("status") or "unknown"
        audit_style = {"pass": "green", "fail": "red"}.get(str(audit_result), "yellow")
        table.add_row("AI audit", f"[{audit_style}]{audit_result}[/]")
        if evaluation.get("needs_human_review"):
            table.add_row("audit review", "[yellow]human review needed[/]")
        action = evaluation.get("recommended_action")
        if action and action != "accept":
            table.add_row("audit action", str(action))

    console.print(Panel(table, title=title, border_style="green" if result.success else "red"))


def _validate_output_path(output: Path | None) -> None:
    if output is None:
        return
    if not output.parent.is_dir():
        console.print(f"[red]Output directory does not exist:[/] {output.parent}")
        raise typer.Exit(2)


def _write_output(output: Path, content: str, *, description: str) -> None:
    try:
        output.write_text(content, encoding="utf-8")
    except OSError as exc:
        console.print(f"[red]Could not write output file {output}:[/] {exc}")
        raise typer.Exit(2) from exc
    console.print(f"[green]Wrote {description} to {output}[/]")


def _result_content(result, output_format: str) -> str:
    if output_format == "markdown":
        return result.markdown or ""
    return result.html or ""


@app.command()
def url(
    target_url: str,
    country: str | None = typer.Option(None, "--country", "-c"),
    render_js: bool = typer.Option(False, "--render-js"),
    premium: bool = typer.Option(False, "--premium"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="Preferred provider"),
    mobile: bool = typer.Option(False, "--mobile", "-m"),
    wait_event: str | None = typer.Option(
        None, "--wait-event", help="domcontentloaded|load|networkidle"
    ),
    wait_selector: str | None = typer.Option(
        None, "--wait-selector", help="CSS selector to wait for"
    ),
    extra_wait: int = typer.Option(0, "--extra-wait", help="Extra wait in ms after page load"),
    block_ads: bool = typer.Option(False, "--block-ads"),
    output_format: str = typer.Option("html", "--format", "-f", help="html|markdown"),
    screenshot: bool = typer.Option(False, "--screenshot"),
    tier: str | None = typer.Option(
        None, "--tier", "-t", help="ScrapeDrive tier: standard|advanced|hyperdrive"
    ),
    meta: bool = typer.Option(False, "--meta", help="Extract and print page metadata as JSON"),
    debug_artifacts: bool = typer.Option(
        False, "--debug-artifacts", help="Save failed response bodies in the telemetry run folder"
    ),
    evaluation_goal: str | None = typer.Option(
        None,
        "--evaluation-goal",
        help="Describe what a usable scrape must contain for the audit evaluator",
    ),
    referer: str | None = typer.Option(
        None, "--referer", help="Referer header (default: auto Google search URL, '' to disable)"
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write scrape content to this file"
    ),
) -> None:
    """Scrape one URL through the gateway.

    Tries providers from cheapest to most expensive until one succeeds.
    Results are cached locally so repeat scrapes are instant and free.
    Domain memory remembers which provider worked, so next time it
    skips straight to the winner.

    Good for: quick one-off scrapes, testing if a site is scrapeable,
    getting raw HTML/markdown for analysis.

    Examples:
      sgw url https://example.com
      sgw url https://example.com --render-js     # JS-heavy SPA
      sgw url https://example.com -p scrapedrive  # force a provider
      sgw url https://example.com --tier advanced  # force ScrapeDrive tier
      sgw url https://example.com --no-cache      # fresh scrape
      sgw url https://example.com --meta          # extract page metadata
      sgw url https://example.com -f markdown -o page.md  # save content
      sgw url https://example.com --referer https://google.com  # spoof referer
    """

    _validate_output_path(output)

    async def run() -> None:
        gateway = _build_gateway(provider)
        metadata = {}
        if tier:
            metadata["start_tier"] = f"scrapedrive:{tier}"
        if debug_artifacts:
            metadata["debug_artifacts"] = True
        if evaluation_goal:
            metadata["evaluation_goal"] = evaluation_goal
        fmt = output_format
        if meta and fmt == "markdown":
            fmt = "html"
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
                    referer=referer,
                    output_format=fmt,
                    metadata=metadata,
                ),
                use_cache=not no_cache,
                use_memory=not no_cache,
            )
        _print_result(result)
        if meta and result.success and result.html:
            page_metadata = _extract_page_metadata(result.html, result.url)
            print(json.dumps(page_metadata, indent=2, ensure_ascii=False))
        elif meta and result.success and not result.html:
            console.print("[yellow]--meta requires HTML content; try without --format markdown[/]")
        if output and result.success:
            _write_output(
                output,
                _result_content(result, fmt),
                description="scrape content",
            )
        _hints("url", target_url)
        if not result.success:
            raise typer.Exit(1)

    asyncio.run(run())


@app.command()
def evaluations(
    root: Path | None = typer.Option(
        None,
        "--root",
        help="Telemetry run root (defaults to telemetry.root from config)",
    ),
    limit: int = typer.Option(500, "--limit", "-n", min=1),
    domain: str | None = typer.Option(None, "--domain"),
    output_format: str = typer.Option("rich", "--format", "-f", help="rich|json"),
) -> None:
    """Summarize saved scrape-quality audits and surface recurring issues.

    This is an evidence and review surface. It never changes providers,
    validators, prompts, or routing automatically.
    """
    from .config import load_config
    from .telemetry import load_recent_reports, summarize_evaluations

    config = load_config()
    reports = load_recent_reports(
        root=root or config.telemetry.root,
        limit=limit,
        domain=domain,
        evaluated_only=True,
    )
    summary = summarize_evaluations(reports)

    if output_format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    if output_format != "rich":
        console.print("[red]--format must be rich or json[/]")
        raise typer.Exit(2)

    console.print(
        Panel(
            f"[bold]{summary['evaluated_runs']}[/] evaluated / "
            f"{summary['runs_scanned']} scanned\n"
            f"[yellow]{len(summary['review_queue'])}[/] need review  "
            f"OpenRouter [cyan]${summary['usage']['cost']:.6f}[/]  "
            f"upstream [cyan]${summary['usage']['upstream_inference_cost']:.6f}[/]\n"
            "[dim]uncalibrated audit signal; no automatic changes[/]",
            title="Scrape quality evaluations",
        )
    )

    counts = Table(show_header=True)
    counts.add_column("Signal")
    counts.add_column("Counts")
    counts.add_row(
        "Verdicts",
        ", ".join(f"{key}: {value}" for key, value in summary["verdict_counts"].items()) or "none",
    )
    counts.add_row(
        "Page types",
        ", ".join(f"{key}: {value}" for key, value in summary["page_type_counts"].items())
        or "none",
    )
    counts.add_row(
        "Root causes",
        ", ".join(f"{key}: {value}" for key, value in summary["root_cause_counts"].items())
        or "none",
    )
    counts.add_row(
        "Issues",
        ", ".join(f"{key}: {value}" for key, value in summary["issue_counts"].items()) or "none",
    )
    counts.add_row(
        "Actions",
        ", ".join(f"{key}: {value}" for key, value in summary["recommended_action_counts"].items())
        or "none",
    )
    counts.add_row(
        "Failed checks",
        ", ".join(f"{key}: {value}" for key, value in summary["check_failure_counts"].items())
        or "none",
    )
    console.print(counts)

    if summary["review_queue"]:
        review = Table(title="Review queue", show_lines=True)
        review.add_column("Run", style="dim")
        review.add_column("URL", style="cyan", max_width=45)
        review.add_column("Verdict")
        review.add_column("Failed checks")
        review.add_column("Next action")
        for item in summary["review_queue"][:20]:
            review.add_row(
                str(item.get("run_id") or "?"),
                str(item.get("url") or "?"),
                str(item.get("verdict") or item.get("status") or "?"),
                ", ".join(item.get("failed_checks") or []) or "-",
                str(item.get("recommended_action") or item.get("error") or "manual review"),
            )
        console.print(review)


@app.command()
def meta(
    target_url: str,
    provider: str | None = typer.Option(None, "--provider", "-p", help="Preferred provider"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    render_js: bool = typer.Option(False, "--render-js", help="Render JS before extracting"),
) -> None:
    """Extract social, structured, and document metadata from a URL.

    Extracts OpenGraph and Twitter Card tags, JSON-LD, canonical and icon URLs,
    charset, and robots directives. These normally live in static HTML, so no
    JavaScript is needed. Prints clean JSON to stdout for jq or scripts.

    Examples:
      sgw meta https://example.com
      sgw meta https://facebook.com/some/post
      sgw meta https://example.com --render-js  # if site needs JS
      sgw meta https://example.com 2>/dev/null | jq '.json_ld'
    """

    async def run() -> None:
        gateway = _build_gateway(provider)
        with console.status(f"[bold cyan]Fetching metadata from {target_url}...", spinner="dots"):
            result = await gateway.scrape(
                ScrapeRequest(
                    target_url,
                    render_js=render_js,
                    output_format="html",
                ),
                use_cache=not no_cache,
                use_memory=not no_cache,
            )
        if not result.success:
            console.print(f"[red]Scrape failed: {result.failure_reason or result.error}[/]")
            raise typer.Exit(1)
        if not result.html:
            console.print("[red]No HTML content returned[/]")
            raise typer.Exit(1)
        page_metadata = _extract_page_metadata(result.html, result.url)
        if page_metadata:
            print(json.dumps(page_metadata, indent=2, ensure_ascii=False))
        else:
            console.print("[yellow]No page metadata found[/]")

    asyncio.run(run())


@app.command()
def telemetry(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of recent reports to show"),
    domain: str | None = typer.Option(None, "--domain", "-d", help="Filter by domain"),
    diagnosis: str | None = typer.Option(None, "--diagnosis", help="Filter by diagnosis code"),
    json_output: bool = typer.Option(False, "--json", help="Print full reports as JSON"),
    summary: bool = typer.Option(False, "--summary", help="Aggregate the selected reports"),
) -> None:
    """Inspect recent scrape telemetry reports."""
    from .telemetry import load_recent_reports, summarize_telemetry

    reports = load_recent_reports(limit=limit, domain=domain, diagnosis=diagnosis)
    if summary:
        aggregate = summarize_telemetry(reports)
        if json_output:
            print(json.dumps(aggregate, indent=2, ensure_ascii=False))
            return
        if not reports:
            console.print("[yellow]No telemetry reports found[/]")
            return
        _print_telemetry_summary(aggregate, limit=limit)
        return
    if json_output:
        print(json.dumps(reports, indent=2, ensure_ascii=False))
        return
    if not reports:
        console.print("[yellow]No telemetry reports found[/]")
        return

    table = Table(title="Recent Scrape Telemetry")
    table.add_column("time", style="dim")
    table.add_column("ok")
    table.add_column("domain")
    table.add_column("provider")
    table.add_column("diagnosis")
    table.add_column("action")
    table.add_column("report", style="dim")
    for report in reports:
        final = report.get("final", {})
        ok = "[green]yes[/]" if report.get("success") else "[red]no[/]"
        table.add_row(
            str(report.get("started_at", ""))[:19],
            ok,
            str(report.get("domain", "")),
            str(final.get("provider", "")),
            str(report.get("diagnosis", "")),
            str(report.get("recommended_next_action", "")),
            str(report.get("_path", "")),
        )
    console.print(table)


def _print_telemetry_summary(summary: dict, *, limit: int) -> None:
    metrics = Table.grid(expand=True)
    metrics.add_column(justify="center")
    metrics.add_column(justify="center")
    metrics.add_column(justify="center")
    metrics.add_column(justify="center")
    metrics.add_row(
        f"[bold]{summary['runs']}[/]\n[dim]Runs analyzed[/]",
        f"[bold]{summary['success_rate_pct']:.1f}%[/]\n[dim]Success rate[/]",
        f"[bold]{summary['average_attempt_count']:.2f}[/]\n[dim]Avg attempts[/]",
        f"[bold]{summary['average_cost']:.2f}[/]\n[dim]Avg cost[/]",
    )
    console.print(Panel(metrics, title=f"Telemetry Summary — Last {limit} Runs"))

    domains = Table(title="Success Rate by Domain")
    domains.add_column("Domain")
    domains.add_column("Runs", justify="right")
    domains.add_column("Succeeded", justify="right")
    domains.add_column("Rate", justify="right")
    for item in summary["domains"]:
        rate = item["success_rate_pct"]
        style = "green" if rate >= 90 else "yellow" if rate >= 70 else "red"
        domains.add_row(
            item["domain"],
            str(item["runs"]),
            str(item["successes"]),
            f"[{style}]{rate:.1f}%[/]",
        )
    console.print(domains)

    diagnoses = Table(title="Most Common Diagnoses")
    diagnoses.add_column("Diagnosis")
    diagnoses.add_column("Runs", justify="right")
    diagnoses.add_column("Share", justify="right")
    for item in summary["diagnoses"]:
        diagnoses.add_row(
            item["diagnosis"],
            str(item["count"]),
            f"{item['share_pct']:.1f}%",
        )
    console.print(diagnoses)

    providers = Table(title="Provider Hit Rate (wins / attempts)")
    providers.add_column("Provider")
    providers.add_column("Attempts", justify="right")
    providers.add_column("Wins", justify="right")
    providers.add_column("Hit rate", justify="right")
    for item in summary["providers"]:
        rate = item["hit_rate_pct"]
        style = "green" if rate >= 80 else "yellow" if rate >= 50 else "red"
        providers.add_row(
            item["provider"],
            str(item["attempts"]),
            str(item["wins"]),
            f"[{style}]{rate:.1f}%[/]",
        )
    console.print(providers)
    non_provider_attempts = summary.get("non_provider_attempts", 0)
    if non_provider_attempts:
        suffix = "record" if non_provider_attempts == 1 else "records"
        console.print(
            f"[dim]({non_provider_attempts} non-provider {suffix} omitted; provider names "
            "come from the resolved registry.)[/]"
        )
    console.print(
        "[dim]Tuning hint: providers with many attempts and a low hit rate are candidates "
        "to move later in the routing order.[/]"
    )


@app.command()
def run(
    urls_file: Path,
    country: str | None = typer.Option(None, "--country", "-c"),
    render_js: bool = typer.Option(False, "--render-js"),
    premium: bool = typer.Option(False, "--premium"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="Preferred provider"),
    mobile: bool = typer.Option(False, "--mobile", "-m"),
    wait_event: str | None = typer.Option(
        None, "--wait-event", help="domcontentloaded|load|networkidle"
    ),
    wait_selector: str | None = typer.Option(
        None, "--wait-selector", help="CSS selector to wait for"
    ),
    extra_wait: int = typer.Option(0, "--extra-wait", help="Extra wait in ms after page load"),
    block_ads: bool = typer.Option(False, "--block-ads"),
    output_format: str = typer.Option("html", "--format", "-f", help="html|markdown"),
    screenshot: bool = typer.Option(False, "--screenshot"),
    tier: str | None = typer.Option(
        None, "--tier", "-t", help="ScrapeDrive tier: standard|advanced|hyperdrive"
    ),
    referer: str | None = typer.Option(
        None, "--referer", help="Referer header (default: auto Google search URL, '' to disable)"
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write successful scrape contents to this file"
    ),
) -> None:
    """Scrape URLs from a text file, one URL per line.

    Scrapes each URL through the gateway and shows a summary table.
    Uses the same provider fallback and caching as 'sgw url'.

    Good for: scraping a known list of pages in bulk. If you also
    need to extract data from each page, use 'sgw recipe' instead —
    it combines scraping and extraction in one step.

    Examples:
      sgw run urls.txt
      sgw run urls.txt --render-js -p scrapedrive
      sgw run urls.txt -f markdown -o pages.md
      sgw run urls.txt --referer https://google.com
    """

    _validate_output_path(output)

    async def execute() -> None:
        gateway = _build_gateway(provider)
        metadata = {}
        if tier:
            metadata["start_tier"] = f"scrapedrive:{tier}"
        urls = [line.strip() for line in urls_file.read_text().splitlines() if line.strip()]
        successes = 0
        total_cost = 0.0
        output_contents: list[str] = []

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
                        referer=referer,
                        output_format=output_format,
                        metadata=metadata,
                    )
                )
            successes += int(result.success)
            total_cost += result.cost_units
            if output and result.success:
                output_contents.append(_result_content(result, output_format))
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
        if output:
            _write_output(
                output,
                "\n".join(output_contents),
                description=f"{len(output_contents)} scrape results",
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
            return (href[len(o) :] or "/", True)
    if href.startswith("/"):
        return (href, True)
    return (href, False)


def _compact_links(
    all_links: list[dict], groups: dict[str, list[int]], base_url: str, limit: int = 0
) -> str:
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
                    suffix = path[len(prefix) :]
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
    limit: int = typer.Option(
        0, "--limit", "-n", help="Max links per directory in compact mode (0=all)"
    ),
) -> None:
    """Extract and group links from a page by semantic location.

    Finds all links on a page, assigns each a numbered index, and
    groups them by where they appear (navigation, main content,
    footer, sidebar). Use 'sgw follow <url> <index>' to scrape
    a specific link by its number.

    Good for: exploring a site's structure, finding pagination links,
    discovering content URLs before bulk extraction. The JSON format
    pipes cleanly to jq; compact format is optimized for LLMs.

    Examples:
      sgw links https://example.com             # rich table
      sgw links https://example.com -f compact  # tree view for LLMs
      sgw links https://example.com -f json     # pipe to jq
      sgw links https://example.com --limit 20  # first 20 only
    """

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
            section_order = [
                "nav",
                "header",
                "main",
                "article",
                "section",
                "aside",
                "footer",
                "other",
            ]
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
    link_id: int = typer.Argument(..., help="Link index from sgw links output"),
    country: str | None = typer.Option(None, "--country", "-c"),
    render_js: bool = typer.Option(False, "--render-js"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="Preferred provider"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Scrape a page, pick a link by index, then scrape that link.

    Two scrapes in one command: first it loads the page to get the
    link list (from cache if available), then scrapes the link you
    picked by index number. Use 'sgw links' first to see the indices.

    Good for: navigating a site step by step — load a page, see its
    links, follow one, see that page's links, follow another. Like
    browsing, but from the terminal.

    Examples:
      sgw links https://example.com         # see link indices
      sgw follow https://example.com 3      # scrape link #3
    """

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
        match = next((lnk for lnk in all_links if lnk["id"] == link_id), None)
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
    "Prices": _re.compile(
        r"(?:[$€£¥₹]\s?\d[\d,. ]*\d|\d[\d,. ]*\d\s?(?:USD|EUR|GBP|RON|lei))", _re.IGNORECASE
    ),
    "Emails": _re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    "Phones": _re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}"),
    "Dates": _re.compile(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}[/.-]\d{1,2}[/.-]\d{1,2}"),
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
            repeated.append(
                {
                    "parent": parent_sel,
                    "selector": selector,
                    "count": count,
                    "sample": sample,
                }
            )

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
    """Detect repeated elements and data patterns in a page.

    Scans the HTML for elements that repeat (product cards, article
    lists, table rows, nav items) and reports what it finds: the CSS
    selector, how many times it repeats, and a sample of the content.
    Also spots prices, dates, and emails.

    Good for: figuring out what's on a page before extracting.
    'sgw detect' is the reconnaissance step — it tells you what
    patterns exist. Then 'sgw extract' pulls the actual data.

    Not useful for: pages that need JavaScript to render (the
    patterns won't be in the raw HTML). Use --render-js.

    Examples:
      sgw detect https://books.toscrape.com
      sgw detect https://example.com --render-js
    """

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

        for name, label in [
            ("prices", "Prices"),
            ("emails", "Emails"),
            ("phones", "Phones"),
            ("dates", "Dates"),
        ]:
            items = patterns.get(name, [])
            if items:
                console.print(f"[bold]{label}[/] ({len(items)} found)")
                for item in items[:10]:
                    console.print(f"  [dim]•[/] {item}")
                console.print()

        if not patterns.get("repeated") and not any(
            patterns.get(k) for k in ("prices", "emails", "phones", "dates")
        ):
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

    skip_tags = {
        "script",
        "style",
        "img",
        "a",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "time",
        "br",
        "hr",
        "i",
        "svg",
        "path",
        "button",
        "input",
        "form",
        "select",
        "noscript",
    }
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
    patterns: list[dict],
    html: str,
    domain: str,
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
        lines.append(f'#{i}: {css} ({p["count"]} items) — "{p["sample"][:60]}"\n{field_str}')

    prompt = (
        f"Domain: {domain}\n\nDetected patterns:\n"
        + "\n".join(lines)
        + "\n\nPick the pattern that is the main content listing "
        "(products, articles, results), not navigation or boilerplate.\n"
        'Reply JSON: {"pick": <number>, "fields": {"<raw>": "<better>", ...}}\n'
        "Only rename unclear field names. Keep title, price, image, href, date as-is."
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
    except llm.UnknownModelError:
        console.print(f"[red]llm error:[/] unknown model: {model_id}\n  available: llm models list")
        return None
    except llm.NeedsKeyException:
        console.print(
            f"[red]llm error:[/] no API key for {model_id or 'default model'}\n  run: llm keys set <name>"
        )
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
    selector: str | None = typer.Option(
        None, "--selector", "-s", help="CSS selector (auto-detect if omitted)"
    ),
    pick: int = typer.Option(
        1, "--pick", "-k", help="Which detected pattern to use (1=top, 2=second, ...)"
    ),
    country: str | None = typer.Option(None, "--country", "-c"),
    render_js: bool = typer.Option(False, "--render-js"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="Preferred provider"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip LLM pattern picking"),
    model: str | None = typer.Option(
        None, "--model", "-m", help="LLM model for pattern picking (default: llm default)"
    ),
    output_format: str = typer.Option("json", "--format", "-f", help="json|csv|rich"),
    limit: int = typer.Option(0, "--limit", "-n", help="Max rows (0=all)"),
) -> None:
    """Extract structured data from repeated page elements.

    The main data extraction command. Finds repeated elements on a
    page (product cards, article lists, search results) and pulls
    structured data from each one — titles, prices, images, links,
    dates — as JSON, CSV, or a rich table.

    By default, an LLM (via your 'llm' CLI config) picks the best
    pattern and gives fields semantic names. This costs a few cents
    the first time, then it's cached per domain forever — repeat
    extractions are instant and free.

    Good for: turning any listing page into structured data without
    writing a custom scraper. Works on product pages, search results,
    blog feeds, directories — anything with repeated elements.

    Not useful for: single-item pages (there's nothing repeated to
    detect), or heavily JS-rendered pages (use --render-js).

    Examples:
      sgw extract https://books.toscrape.com              # auto-detect
      sgw extract https://books.toscrape.com -f csv       # CSV output
      sgw extract https://books.toscrape.com -s "ol > li" # manual selector
      sgw extract https://books.toscrape.com --no-llm     # skip LLM
      sgw extract https://books.toscrape.com -m proxy-pro # use better model
    """

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
                    f"  hint: run 'sgw detect {target_url}' to see available patterns"
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
                f"  hint: try 'sgw detect {target_url}' and use --selector or --pick N"
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
    """Show scrape history and structural changes for a URL.

    Every time you scrape a URL, sg fingerprints the page (title,
    link count, headings, text length) and stores it. This command
    shows the timeline: when you scraped, which provider worked,
    and what changed between scrapes (new links added, title changed,
    content grew/shrank).

    Good for: monitoring if a page changed since last time, catching
    layout changes that might break extraction, or building a history
    of a page over time.

    Examples:
      sgw history https://example.com
      sgw history https://example.com -n 5    # last 5 scrapes only
    """
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
        console.print("\n[bold]Latest fingerprint:[/]")
        console.print(f"  title: [cyan]{latest.get('title', '')}[/]")
        console.print(
            f"  links: {latest.get('link_count', 0)}  images: {latest.get('image_count', 0)}  "
            f"forms: {latest.get('form_count', 0)}  prices: {latest.get('price_count', 0)}"
        )
        heads = latest.get("headings", [])
        if heads:
            console.print(f"  headings: {', '.join(heads[:5])}")

    _hints("history", target_url)
    raise typer.Exit(0)


@app.command()
def recipe(
    recipe_file: Path,
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would run without scraping"),
    output_file: str | None = typer.Option(
        None, "--output", "-o", help="Override output file path"
    ),
) -> None:
    """Run a saved scrape+extract recipe from a YAML file.

    Saves you from retyping the same sgw extract command with all its
    flags every time. Write the URLs, scrape settings, and extraction
    config once as YAML, then replay with one command. Results from
    multiple URLs are combined into a single output file.

    Good for: monitoring a product listing regularly, scraping the
    same set of sites with specific settings you don't want to
    remember, or sharing a scraping workflow with someone else —
    hand them a YAML file instead of a bash command.

    Not useful for: one-off scrapes. Just use 'sgw extract' directly.

    Recipe format:

        url: https://books.toscrape.com       # single URL
        # OR
        urls:                                   # multiple URLs
          - https://books.toscrape.com
          - https://books.toscrape.com/catalogue/page-2.html

        scrape:                                 # optional scrape settings
          provider: scrapedrive
          country: us
          render_js: true

        extract:                                # optional extraction step
          selector: "ol.row > li"
          format: json                          # json|csv
          limit: 20
          model: proxy-flash-lite
          no_llm: false

        output: results.json                    # optional output file

    Examples:
      sgw recipe books.yml                  # run the recipe
      sgw recipe books.yml --dry-run        # preview without scraping
      sgw recipe books.yml -o results.csv   # override output path
    """
    import json

    import yaml

    if not recipe_file.exists():
        console.print(f"[red]error:[/] recipe file not found: {recipe_file}")
        raise typer.Exit(1)

    spec = yaml.safe_load(recipe_file.read_text())
    if not spec:
        console.print(f"[red]error:[/] empty recipe file: {recipe_file}")
        raise typer.Exit(1)

    urls = spec.get("urls", [])
    if not urls and spec.get("url"):
        urls = [spec["url"]]
    if not urls:
        console.print("[red]error:[/] recipe must have 'url' or 'urls'")
        raise typer.Exit(1)

    scrape_opts = spec.get("scrape", {})
    extract_opts = spec.get("extract", {})
    dest = output_file or spec.get("output")
    do_extract = bool(extract_opts) or "extract" in spec

    if dry_run:
        console.print(f"\n[bold]Recipe:[/] {recipe_file.name}")
        console.print(f"  URLs: {len(urls)}")
        for u in urls[:5]:
            console.print(f"    [cyan]{u}[/]")
        if len(urls) > 5:
            console.print(f"    [dim]... and {len(urls) - 5} more[/]")
        if scrape_opts:
            console.print(f"  Scrape: {scrape_opts}")
        if do_extract:
            console.print(f"  Extract: {extract_opts}")
        if dest:
            console.print(f"  Output: {dest}")
        else:
            console.print("  Output: stdout")
        raise typer.Exit(0)

    async def run_recipe() -> None:
        from .config import load_config
        from .memory import DomainMemory

        gateway = _build_gateway(scrape_opts.get("provider"))
        config = load_config()
        memory = DomainMemory(db_path=config.memory_path)

        all_rows: list[dict] = []
        fmt = extract_opts.get("format", "json")
        sel = extract_opts.get("selector")
        pick = extract_opts.get("pick", 1)
        no_llm = extract_opts.get("no_llm", False)
        llm_model = extract_opts.get("model")
        row_limit = extract_opts.get("limit", 0)

        for i, target_url in enumerate(urls, 1):
            if not target_url.startswith(("http://", "https://")):
                target_url = f"https://{target_url}"

            label = f"[{i}/{len(urls)}] {target_url}"
            with console.status(f"[bold cyan]{label}...", spinner="dots"):
                result = await gateway.scrape(
                    ScrapeRequest(
                        target_url,
                        country=scrape_opts.get("country"),
                        render_js=scrape_opts.get("render_js", False),
                        premium=scrape_opts.get("premium", False),
                        mobile=scrape_opts.get("mobile", False),
                    ),
                    use_cache=not scrape_opts.get("no_cache", False),
                    use_memory=not scrape_opts.get("no_cache", False),
                )

            if not result.success or not result.html:
                console.print(f"  [red]FAIL[/]  {target_url} — {result.failure_reason or 'empty'}")
                continue

            console.print(f"  [green]OK[/]    {target_url} — {len(result.html):,} chars")

            if not do_extract:
                continue

            domain = DomainMemory.domain_for_url(result.url)
            field_map: dict[str, str] = {}

            if sel:
                rows, desc = _extract_rows(result.html, sel)
            elif not no_llm:
                cached = memory.get_extraction(domain)
                if cached:
                    sel_cached, field_map = cached
                    rows, desc = _extract_rows(result.html, sel_cached)
                    if not rows:
                        memory.learn_extraction(domain, "", {})
                        rows, desc = _extract_rows(result.html, pick=pick)
                else:
                    patterns = _detect_patterns(result.html)
                    repeated = patterns.get("repeated", [])
                    if repeated:
                        llm_result = _llm_pick_pattern(repeated, result.html, domain, llm_model)
                        if llm_result:
                            llm_sel, field_map = llm_result
                            memory.learn_extraction(domain, llm_sel, field_map)
                            rows, desc = _extract_rows(result.html, llm_sel)
                        else:
                            rows, desc = _extract_rows(result.html, pick=pick)
                    else:
                        rows, desc = [], "no repeated elements"
            else:
                rows, desc = _extract_rows(result.html, pick=pick)

            rows = _apply_field_map(rows, field_map)
            if row_limit:
                rows = rows[:row_limit]

            console.print(f"         [dim]{len(rows)} rows — {desc}[/]")

            for row in rows:
                row["_source_url"] = target_url
            all_rows.extend(rows)

        if not do_extract:
            console.print(f"\n[bold]{len(urls)}[/] URLs scraped.")
            raise typer.Exit(0)

        console.print(f"\n[bold]{len(all_rows)}[/] total rows from [bold]{len(urls)}[/] URLs")

        output_text = ""
        if fmt == "csv":
            import csv
            import io

            all_keys = list(dict.fromkeys(k for r in all_rows for k in r))
            out = io.StringIO()
            writer = csv.DictWriter(out, fieldnames=all_keys)
            writer.writeheader()
            writer.writerows(all_rows)
            output_text = out.getvalue()
        else:
            output_text = json.dumps(all_rows, indent=2, ensure_ascii=False)

        if dest:
            Path(dest).write_text(output_text)
            console.print(f"Saved to [cyan]{dest}[/]")
        else:
            print(output_text, end="")

    asyncio.run(run_recipe())


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    max_results: int = typer.Option(10, "--max", "-n", help="Max results to return"),
    region: str = typer.Option(
        "us-en", "--region", "-r", help="Region (us-en, wt-wt=global, uk-en, etc.)"
    ),
    timelimit: str | None = typer.Option(
        None, "--time", "-t", help="Time filter: d(ay), w(eek), m(onth), y(ear)"
    ),
    backend: str = typer.Option(
        "auto", "--backend", "-b", help="Backend: auto, bing, duckduckgo, google, brave"
    ),
    proxy: bool = typer.Option(
        False, "--proxy", help="Route through SCRAPE_PROXY_URL (Evomi residential)"
    ),
    output_format: str = typer.Option("rich", "--format", "-f", help="rich|json|urls"),
) -> None:
    """Search the web via DuckDuckGo and return results.

    Uses ddgs library for web search with optional proxy routing
    through your configured residential proxy (SCRAPE_PROXY_URL).

    Examples:
      sgw search "python web scraping"
      sgw search "site:github.com scraping" -n 20
      sgw search "price tracker" --proxy          # via Evomi residential
      sgw search "news today" -t d -f json        # today only, JSON output
      sgw search "best laptops" -f urls            # just URLs, one per line
    """
    import os

    from ddgs import DDGS

    proxy_url = None
    if proxy:
        proxy_url = os.environ.get("SCRAPE_PROXY_URL")
        if not proxy_url:
            console.print("[red]--proxy requires SCRAPE_PROXY_URL in environment[/]")
            raise typer.Exit(1)

    with console.status("[bold cyan]Searching...", spinner="dots"):
        ddgs = DDGS(proxy=proxy_url)
        results = ddgs.text(
            query,
            region=region,
            safesearch="moderate",
            timelimit=timelimit,
            max_results=max_results,
            backend=backend,
        )

    if not results:
        console.print("[yellow]No results found.[/]")
        raise typer.Exit(1)

    if output_format == "json":
        print(json.dumps(results, indent=2, ensure_ascii=False))
    elif output_format == "urls":
        for r in results:
            print(r["href"])
    else:
        table = Table(show_lines=False)
        table.add_column("#", style="dim", width=4)
        table.add_column("Title", style="cyan", max_width=50)
        table.add_column("URL", max_width=60)
        table.add_column("Snippet", style="dim", max_width=40)
        for i, r in enumerate(results, 1):
            table.add_row(str(i), r.get("title", ""), r.get("href", ""), r.get("body", "")[:80])
        console.print(table)
        console.print(f"\n[dim]{len(results)} results for[/] [bold]{query}[/]")
        _hints("search", query=query)

    raise typer.Exit(0)


@app.command()
def selftest() -> None:
    """Run a live smoke test against safe public URLs.

    Scrapes a few known-safe sites (example.com, httpbin.org) to
    verify that sg is installed correctly and can make HTTP requests.
    Uses only the free raw_http provider, no API keys needed.

    Good for: checking that sgw works after installation or config
    changes. Not a full test suite — run 'pytest' for that.

    Examples:
      sgw selftest
    """

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


@app.command()
def providers():
    """List all available providers — built-in, installed packages, and local extensions.

    Good for:
      Checking which providers sg can see, whether an extension loaded,
      and what capabilities each provider has (country, JS rendering, etc.)

    Not useful for:
      Changing provider order — that's in scrape-gateway.yml.

    Examples:
      sgw providers
    """
    from .discovery import EXTENSIONS_DIR, discover_providers_with_sources

    available = discover_providers_with_sources()
    if not available:
        console.print("[red]No providers found.[/]")
        raise typer.Exit(1)

    table = Table(title="Available Providers", show_lines=True)
    table.add_column("Name", style="cyan")
    table.add_column("Cost", justify="right")
    table.add_column("Capabilities")
    table.add_column("Source", style="dim")

    for name, (cls, source) in sorted(available.items(), key=lambda x: x[1][0].cost_rank):
        caps = ", ".join(sorted(cls.capabilities)) if cls.capabilities else "html"
        table.add_row(name, str(cls.cost_rank), caps, source)

    console.print(table)
    console.print(f"\n[dim]Extensions directory: {EXTENSIONS_DIR}[/]")
    console.print(
        "[dim]Drop a .py file there with a ProviderAdapter subclass to add a provider.[/]"
    )
    console.print("[dim]sgw extensions                # browse the official extension registry[/]")


REGISTRY_URL = "https://raw.githubusercontent.com/testy-cool/scrape-gateway/main/registry.yml"


@app.command()
def extensions(
    install: str | None = typer.Argument(None, help="Extension name to install"),
):
    """Browse or install extensions from the official registry.

    Good for:
      Discovering community providers, installing them in one command.
      The registry is curated — only reviewed extensions are listed.

    Examples:
      sgw extensions              # list available extensions
      sgw extensions sg-playwright  # install an extension by name
    """
    import subprocess
    import sys

    import httpx
    import yaml

    entries = None
    try:
        resp = httpx.get(REGISTRY_URL, timeout=10, follow_redirects=True)
        resp.raise_for_status()
        entries = yaml.safe_load(resp.text) or []
    except Exception:
        pass
    if entries is None:
        from .config import _PROJECT_ROOT

        local = _PROJECT_ROOT / "registry.yml"
        if local.exists():
            entries = yaml.safe_load(local.read_text()) or []
        else:
            console.print("[red]Failed to fetch registry and no local copy found.[/]")
            raise typer.Exit(1) from None

    if install:
        match = next((e for e in entries if e["name"] == install), None)
        if not match:
            console.print(f"[red]Extension '{install}' not found in registry.[/]")
            raise typer.Exit(1)
        if match.get("status") == "planned":
            console.print(f"[yellow]{install} is planned but not published yet.[/]")
            console.print(f"[dim]Track progress: {match.get('url', 'n/a')}[/]")
            raise typer.Exit(1)
        pkg = match.get("package", install)
        console.print(f"[cyan]Installing {pkg} into sgw's environment...[/]")
        result = subprocess.run(
            ["uv", "pip", "install", "--python", sys.executable, pkg],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            console.print(f"[green]Installed {pkg}. Run `sgw providers` to verify.[/]")
        else:
            console.print(f"[red]Failed:[/] {result.stderr.strip()}")
            raise typer.Exit(1)
        return

    from importlib.metadata import entry_points

    from .discovery import discover_providers

    installed_providers = set(discover_providers())
    installed_commands = {ep.name for ep in entry_points(group="scrape_gateway.commands")}

    table = Table(title="Extension Registry", show_lines=True)
    table.add_column("Name", style="cyan")
    table.add_column("Type")
    table.add_column("Description")
    table.add_column("Status")

    for entry in entries:
        name = entry["name"]
        ext_type = entry.get("type", "provider")
        if name in installed_providers or name in installed_commands:
            status = "[green]installed[/]"
        elif entry.get("status") == "planned":
            status = "[yellow]planned[/]"
        else:
            status = "[dim]available[/]"
        table.add_row(name, ext_type, entry.get("description", ""), status)

    console.print(table)
    console.print("\n[dim]Install: sgw extensions <name>[/]")
    console.print(f"[dim]Submit yours: {REGISTRY_URL.replace('/main/registry.yml', '')}[/]")


# -- Provider API key env vars ------------------------------------------------

PROVIDER_API_KEYS: dict[str, tuple[str, str]] = {
    "scrapedrive": ("SCRAPEDRIVE_API_KEY", "https://scrapedrive.com"),
    "scrape_do": ("SCRAPE_DO_TOKEN", "https://scrape.do"),
    "scrapingbee": ("SCRAPINGBEE_API_KEY", "https://scrapingbee.com"),
    "scraperapi": ("SCRAPERAPI_API_KEY", "https://scraperapi.com"),
}


@app.command()
def setup():
    """Interactive setup — choose which providers to activate and set API keys.

    Writes scrape-gateway.yml and .env in the current directory.
    Run this once after installing, or again to change your config.

    Examples:
      sgw setup
    """
    import os

    import yaml

    from .discovery import discover_providers

    available = discover_providers()
    if not available:
        console.print("[red]No providers found.[/]")
        raise typer.Exit(1)

    console.print("\n[bold]sgw setup[/] — choose which providers to activate\n")

    free_providers = []
    paid_providers = []
    for name, cls in sorted(available.items(), key=lambda x: x[1].cost_rank):
        if name in PROVIDER_API_KEYS:
            paid_providers.append((name, cls))
        else:
            free_providers.append((name, cls))

    enabled = []
    env_vars: dict[str, str] = {}

    console.print("[bold green]Free providers[/] (no API key needed):")
    for name, cls in free_providers:
        caps = ", ".join(sorted(cls.capabilities))
        answer = input(f"  Enable {name} ({caps})? [Y/n] ").strip().lower()
        if not answer or answer in ("y", "yes"):
            enabled.append(name)
            console.print(f"    [green]✓[/] {name}")
        else:
            console.print(f"    [dim]✗ {name}[/]")

    console.print("\n[bold yellow]Paid providers[/] (need API keys):")
    for name, cls in paid_providers:
        env_var, signup_url = PROVIDER_API_KEYS[name]
        caps = ", ".join(sorted(cls.capabilities))
        existing_key = os.getenv(env_var, "")
        hint = " [dim](key already set)[/]" if existing_key else ""
        answer = input(f"  Enable {name} ({caps})?{hint} [y/N] ").strip().lower()
        if answer in ("y", "yes"):
            if existing_key:
                use_existing = input(f"    Keep existing {env_var}? [Y/n] ").strip().lower()
                if not use_existing or use_existing in ("y", "yes"):
                    env_vars[env_var] = existing_key
                else:
                    key = input(f"    {env_var}: ").strip()
                    if key:
                        env_vars[env_var] = key
            else:
                console.print(f"    [dim]Sign up: {signup_url}[/]")
                key = input(f"    {env_var}: ").strip()
                if key:
                    env_vars[env_var] = key
            enabled.append(name)
            console.print(f"    [green]✓[/] {name}")
        else:
            console.print(f"    [dim]✗ {name}[/]")

    if not enabled:
        console.print("\n[red]No providers enabled. At least one is required.[/]")
        raise typer.Exit(1)

    config = {
        "providers": [{"name": n, "enabled": True} for n in enabled],
        "cache": {"ttl": "24h"},
        "strategy": {"mode": "cheapest_successful"},
    }

    config_path = Path("scrape-gateway.yml")
    config_path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))
    console.print(f"\n[green]Wrote {config_path}[/]")

    if env_vars:
        env_path = Path(".env")
        existing_lines = []
        if env_path.exists():
            existing_lines = [
                line
                for line in env_path.read_text().splitlines()
                if not any(line.startswith(k + "=") for k in env_vars)
            ]
        all_lines = existing_lines + [f'{k}="{v}"' for k, v in env_vars.items()]
        env_path.write_text("\n".join(all_lines) + "\n")
        console.print(f"[green]Wrote {env_path}[/]")

    console.print(f"\n[bold]Ready![/] {len(enabled)} providers active.")
    console.print("[dim]sgw selftest    # verify everything works[/]")
    console.print("[dim]sgw providers   # see what's loaded[/]")


load_command_extensions(app)
