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
