from __future__ import annotations

import json
from urllib.parse import urlparse

import typer
from rich.console import Console
from rich.table import Table

console = Console(stderr=True)


def _normalize_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return f"https://{url}"
    return url


def _base_url(url: str) -> str:
    parsed = urlparse(_normalize_url(url))
    if not parsed.netloc:
        raise ValueError(f"Invalid URL: {url}")
    return f"{parsed.scheme}://{parsed.netloc}"


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
        external: bool = typer.Option(False, "--external", help="Include external URLs"),
        sleep_time: float = typer.Option(
            0.0, "--sleep", help="Seconds to wait between sitemap requests"
        ),
        max_sitemaps: int = typer.Option(
            10000, "--max-sitemaps", help="Maximum sitemap files to process"
        ),
        discover_only: bool = typer.Option(
            False,
            "--discover-only",
            help="Only print sitemap URLs declared in robots.txt; do not expand page URLs",
        ),
    ) -> None:
        """Find and expand sitemaps with trafilatura.

        By default this prints page URLs extracted from sitemap files. Use
        --discover-only when you only want sitemap XML URLs advertised in
        robots.txt.
        """
        fmt = output_format.lower()
        if fmt not in {"json", "txt", "rich"}:
            console.print("[red]Unsupported format. Use json, txt, or rich.[/]")
            raise typer.Exit(1)

        try:
            from trafilatura.sitemaps import find_robots_sitemaps, sitemap_search
        except ImportError:
            console.print(
                "[red]Missing dependency: trafilatura[/]\n"
                "Install this extension with `pip install -e . -e extensions/sg-sitemap` "
                "or install trafilatura in the same environment as sgw."
            )
            raise typer.Exit(1) from None

        try:
            normalized = _normalize_url(target_url)
            if discover_only:
                urls = find_robots_sitemaps(_base_url(normalized))
                kind = "sitemaps"
                title = f"Sitemaps declared in robots.txt for {_base_url(normalized)}"
            else:
                urls = sitemap_search(
                    normalized,
                    target_lang=lang,
                    external=external,
                    sleep_time=sleep_time,
                    max_sitemaps=max_sitemaps,
                )
                kind = "pages"
                title = f"Sitemap page URLs for {normalized}"
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Sitemap discovery failed:[/] {exc}")
            raise typer.Exit(1) from exc

        if limit and not discover_only:
            output_urls = urls[:limit]
        else:
            output_urls = urls

        if fmt == "json":
            _print_urls_json(target_url, output_urls, kind=kind)
        elif fmt == "txt":
            for url in output_urls:
                print(url)
        else:
            _print_urls_rich(urls, title=title, limit=limit if not discover_only else 0)

        if not urls:
            console.print("[yellow]No sitemap URLs found.[/]")
            raise typer.Exit(1)
