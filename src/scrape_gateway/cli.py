from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from .models import ScrapeRequest
from .router import ScrapeGateway

app = typer.Typer(help="Scrape Gateway: cache, route, escalate, remember.")


@app.command()
def url(
    target_url: str,
    country: str | None = typer.Option(None, "--country", "-c"),
    render_js: bool = typer.Option(False, "--render-js"),
    premium: bool = typer.Option(False, "--premium"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Scrape one URL through the gateway."""

    async def run() -> None:
        gateway = ScrapeGateway()
        result = await gateway.scrape(
            ScrapeRequest(target_url, country=country, render_js=render_js, premium=premium),
            use_cache=not no_cache,
        )
        status = "OK" if result.success else "FAIL"
        typer.echo(
            f"{status} provider={result.provider} route={result.route} status={result.status_code} cost={result.cost_units}"
        )
        if result.failure_reason:
            typer.echo(f"failure_reason={result.failure_reason}")

    asyncio.run(run())


@app.command()
def run(
    urls_file: Path,
    country: str | None = typer.Option(None, "--country", "-c"),
    render_js: bool = typer.Option(False, "--render-js"),
    premium: bool = typer.Option(False, "--premium"),
) -> None:
    """Scrape URLs from a text file, one URL per line."""

    async def execute() -> None:
        gateway = ScrapeGateway()
        urls = [line.strip() for line in urls_file.read_text().splitlines() if line.strip()]
        successes = 0
        total_cost = 0.0
        for item in urls:
            result = await gateway.scrape(
                ScrapeRequest(item, country=country, render_js=render_js, premium=premium)
            )
            successes += int(result.success)
            total_cost += result.cost_units
            typer.echo(
                f"{item}\t{result.success}\t{result.provider}\t{result.route}\t{result.failure_reason or ''}"
            )
        typer.echo(f"\nProcessed={len(urls)} Successes={successes} CostUnits={total_cost}")

    asyncio.run(execute())
