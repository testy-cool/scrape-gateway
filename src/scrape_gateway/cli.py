from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from .models import ScrapeRequest
from .router import ScrapeGateway

app = typer.Typer(help="Scrape Gateway: cache, route, escalate, remember.")


def _format_result(result) -> str:
    status = "OK" if result.success else "FAIL"
    parts = [
        f"{status}",
        f"provider={result.provider}",
        f"route={result.route}",
        f"status={result.status_code}",
        f"cost={result.cost_units}",
    ]
    if result.content_validated is not None:
        parts.append(f"validated={result.content_validated}")
    if result.block_type:
        parts.append(f"block={result.block_type}")
    if result.failure_reason:
        parts.append(f"reason={result.failure_reason}")
    if result.validation_detail:
        parts.append(f"detail={result.validation_detail}")
    if result.html:
        parts.append(f"chars={len(result.html)}")
    return " ".join(parts)


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
        gateway = ScrapeGateway.from_config()
        result = await gateway.scrape(
            ScrapeRequest(target_url, country=country, render_js=render_js, premium=premium),
            use_cache=not no_cache,
        )
        typer.echo(_format_result(result))

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
        gateway = ScrapeGateway.from_config()
        urls = [line.strip() for line in urls_file.read_text().splitlines() if line.strip()]
        successes = 0
        total_cost = 0.0
        for item in urls:
            result = await gateway.scrape(
                ScrapeRequest(item, country=country, render_js=render_js, premium=premium)
            )
            successes += int(result.success)
            total_cost += result.cost_units
            typer.echo(f"{item}\t{_format_result(result)}")
        typer.echo(f"\nProcessed={len(urls)} Successes={successes} CostUnits={total_cost}")

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
            result = await gateway.scrape(ScrapeRequest(target_url), use_cache=False)
            ok = (result.success and result.content_validated) or (
                not result.success and result.failure_reason is not None
            )
            icon = "PASS" if ok else "FAIL"
            if ok:
                passed += 1
            else:
                failed += 1
            typer.echo(f"  {icon}  {description}")
            typer.echo(f"       {_format_result(result)}")

        typer.echo(f"\n{passed} passed, {failed} failed")
        raise typer.Exit(code=1 if failed else 0)

    asyncio.run(run_tests())
