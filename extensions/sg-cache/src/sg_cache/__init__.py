from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import typer
from rich.console import Console
from rich.table import Table

console = Console(stderr=True)
cache_app = typer.Typer(help="Inspect and manage scrape-gateway artifact cache.")


@dataclass(slots=True)
class CacheEntry:
    key: str
    folder: str
    url: str | None
    provider: str | None
    route: str | None
    fetched_at: float | None
    age_seconds: float | None
    expired: bool
    size_bytes: int
    has_html: bool
    has_markdown: bool
    has_screenshot: bool
    meta_error: str | None = None


def _load_config():
    from scrape_gateway.config import load_config

    return load_config()


def _cache_root(root: str | None = None) -> Path:
    if root:
        return Path(root)
    return Path(_load_config().cache.root)


def _cache_ttl(ttl: int | None = None) -> int:
    if ttl is not None:
        return ttl
    return _load_config().cache.ttl_seconds


def _folder_size(folder: Path) -> int:
    total = 0
    for path in folder.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total


def _load_meta(folder: Path) -> tuple[dict, str | None]:
    meta_path = folder / "meta.json"
    if not meta_path.exists():
        return {}, "missing meta.json"
    try:
        return json.loads(meta_path.read_text(encoding="utf-8")), None
    except Exception as exc:  # noqa: BLE001
        return {}, f"invalid meta.json: {exc}"


def _entry_from_folder(folder: Path, *, ttl_seconds: int, now: float | None = None) -> CacheEntry:
    now = now or time.time()
    meta, meta_error = _load_meta(folder)
    fetched_at = meta.get("fetched_at")
    age_seconds = (now - float(fetched_at)) if fetched_at else None
    expired = bool(ttl_seconds > 0 and age_seconds is not None and age_seconds > ttl_seconds)
    return CacheEntry(
        key=folder.name,
        folder=str(folder),
        url=meta.get("url"),
        provider=meta.get("provider"),
        route=meta.get("route"),
        fetched_at=float(fetched_at) if fetched_at else None,
        age_seconds=age_seconds,
        expired=expired,
        size_bytes=_folder_size(folder),
        has_html=(folder / "page.html").exists(),
        has_markdown=(folder / "page.md").exists(),
        has_screenshot=(folder / "screenshot.bin").exists(),
        meta_error=meta_error,
    )


def _iter_entries(root: Path, *, ttl_seconds: int) -> list[CacheEntry]:
    if not root.exists():
        return []
    entries = [
        _entry_from_folder(folder, ttl_seconds=ttl_seconds)
        for folder in root.iterdir()
        if folder.is_dir()
    ]
    return sorted(entries, key=lambda e: e.fetched_at or 0, reverse=True)


def _domain_matches(url: str | None, domain: str) -> bool:
    if not url:
        return False
    hostname = (urlparse(url).hostname or "").lower()
    needle = domain.lower().lstrip(".")
    return hostname == needle or hostname.endswith("." + needle)


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def _human_age(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=UTC).isoformat().replace("+00:00", "Z")


def _entry_json(entry: CacheEntry) -> dict:
    data = asdict(entry)
    data["fetched_at_iso"] = _iso(entry.fetched_at)
    return data


def _select_entries(
    entries: list[CacheEntry],
    *,
    domain: str | None = None,
    expired_only: bool = False,
) -> list[CacheEntry]:
    if domain:
        entries = [entry for entry in entries if _domain_matches(entry.url, domain)]
    if expired_only:
        entries = [entry for entry in entries if entry.expired]
    return entries


@cache_app.command("stats")
def stats(
    root: str | None = typer.Option(None, "--root", help="Cache root override"),
    output_format: str = typer.Option("rich", "--format", "-f", help="rich|json"),
    ttl_seconds: int | None = typer.Option(None, "--ttl-seconds", help="TTL override"),
) -> None:
    """Print cache size, entry count, providers, domains, and expiration counts."""
    cache_root = _cache_root(root)
    ttl = _cache_ttl(ttl_seconds)
    entries = _iter_entries(cache_root, ttl_seconds=ttl)

    domains = sorted({urlparse(e.url).hostname for e in entries if e.url})
    providers: dict[str, int] = {}
    for entry in entries:
        provider = entry.provider or "unknown"
        providers[provider] = providers.get(provider, 0) + 1

    payload = {
        "root": str(cache_root),
        "ttl_seconds": ttl,
        "entries": len(entries),
        "expired": sum(1 for e in entries if e.expired),
        "total_size_bytes": sum(e.size_bytes for e in entries),
        "domains": len(domains),
        "providers": providers,
        "with_html": sum(1 for e in entries if e.has_html),
        "with_markdown": sum(1 for e in entries if e.has_markdown),
        "with_screenshot": sum(1 for e in entries if e.has_screenshot),
    }

    if output_format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    if output_format != "rich":
        console.print("[red]Unsupported format. Use rich or json.[/]")
        raise typer.Exit(1)

    table = Table(title="sgw cache stats")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    table.add_row("Root", str(cache_root))
    table.add_row("Entries", str(payload["entries"]))
    table.add_row("Expired", str(payload["expired"]))
    table.add_row("Total size", _human_size(payload["total_size_bytes"]))
    table.add_row("Domains", str(payload["domains"]))
    table.add_row("HTML", str(payload["with_html"]))
    table.add_row("Markdown", str(payload["with_markdown"]))
    table.add_row("Screenshots", str(payload["with_screenshot"]))
    console.print(table)

    if providers:
        provider_table = Table(title="Providers")
        provider_table.add_column("Provider", style="cyan")
        provider_table.add_column("Entries", justify="right")
        for provider, count in sorted(providers.items(), key=lambda item: (-item[1], item[0])):
            provider_table.add_row(provider, str(count))
        console.print(provider_table)


@cache_app.command("ls")
@cache_app.command("list")
def list_entries(
    root: str | None = typer.Option(None, "--root", help="Cache root override"),
    domain: str | None = typer.Option(None, "--domain", "-d", help="Filter by domain"),
    expired: bool = typer.Option(False, "--expired", help="Only show expired entries"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max entries to print (0=all)"),
    output_format: str = typer.Option("rich", "--format", "-f", help="rich|json|urls|keys"),
    ttl_seconds: int | None = typer.Option(None, "--ttl-seconds", help="TTL override"),
) -> None:
    """List cached URLs and their artifact keys."""
    cache_root = _cache_root(root)
    entries = _select_entries(
        _iter_entries(cache_root, ttl_seconds=_cache_ttl(ttl_seconds)),
        domain=domain,
        expired_only=expired,
    )
    shown = entries[:limit] if limit else entries

    if output_format == "json":
        print(json.dumps([_entry_json(entry) for entry in shown], indent=2, ensure_ascii=False))
        return
    if output_format == "urls":
        for entry in shown:
            if entry.url:
                print(entry.url)
        return
    if output_format == "keys":
        for entry in shown:
            print(entry.key)
        return
    if output_format != "rich":
        console.print("[red]Unsupported format. Use rich, json, urls, or keys.[/]")
        raise typer.Exit(1)

    table = Table(title=f"sgw cache entries ({len(entries)})", show_lines=False)
    table.add_column("Age", justify="right", style="dim")
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Provider")
    table.add_column("Size", justify="right")
    table.add_column("URL", overflow="fold")
    for entry in shown:
        age = _human_age(entry.age_seconds)
        if entry.expired:
            age = f"[yellow]{age}[/]"
        table.add_row(age, entry.key, entry.provider or "-", _human_size(entry.size_bytes), entry.url or "-")
    console.print(table)
    if limit and len(entries) > limit:
        console.print(f"[dim]Showing {limit} of {len(entries)} entries[/]")


@cache_app.command("show")
def show(
    target: str = typer.Argument(..., help="Cached URL or artifact key"),
    root: str | None = typer.Option(None, "--root", help="Cache root override"),
    render_js: bool = typer.Option(False, "--render-js", help="Use the JS-rendered URL cache key"),
    output_format: str = typer.Option("markdown", "--format", "-f", help="markdown|html|meta|path"),
    ttl_seconds: int | None = typer.Option(None, "--ttl-seconds", help="TTL override"),
) -> None:
    """Print cached markdown/html/meta for a URL or artifact key."""
    from scrape_gateway.cache import ArtifactCache

    cache_root = _cache_root(root)
    if target.startswith(("http://", "https://")):
        folder = ArtifactCache(root=cache_root, ttl_seconds=_cache_ttl(ttl_seconds)).paths_for_url(
            target, render_js=render_js
        )["folder"]
    else:
        folder = cache_root / target

    if not folder.exists():
        console.print(f"[red]Cache entry not found:[/] {target}")
        raise typer.Exit(1)

    if output_format == "path":
        print(folder)
        return
    if output_format == "meta":
        entry = _entry_from_folder(folder, ttl_seconds=_cache_ttl(ttl_seconds))
        print(json.dumps(_entry_json(entry), indent=2, ensure_ascii=False))
        return

    path = folder / ("page.html" if output_format == "html" else "page.md")
    if output_format not in {"markdown", "html"}:
        console.print("[red]Unsupported format. Use markdown, html, meta, or path.[/]")
        raise typer.Exit(1)
    if not path.exists():
        console.print(f"[red]Cached {output_format} not found:[/] {path}")
        raise typer.Exit(1)
    print(path.read_text(encoding="utf-8"), end="")


@cache_app.command("purge")
def purge(
    target: str | None = typer.Argument(None, help="Cached URL or artifact key"),
    root: str | None = typer.Option(None, "--root", help="Cache root override"),
    domain: str | None = typer.Option(None, "--domain", "-d", help="Purge entries for a domain"),
    expired: bool = typer.Option(False, "--expired", help="Purge expired entries"),
    all_entries: bool = typer.Option(False, "--all", help="Purge all cache entries"),
    render_js: bool = typer.Option(False, "--render-js", help="Use the JS-rendered URL cache key"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm bulk purge"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show entries without deleting"),
    ttl_seconds: int | None = typer.Option(None, "--ttl-seconds", help="TTL override"),
) -> None:
    """Delete cache entries by URL/key, domain, expiration, or all entries."""
    from scrape_gateway.cache import ArtifactCache

    selectors = [bool(target), bool(domain), expired, all_entries]
    if sum(selectors) != 1:
        console.print("[red]Choose exactly one: target, --domain, --expired, or --all.[/]")
        raise typer.Exit(1)

    cache_root = _cache_root(root)
    ttl = _cache_ttl(ttl_seconds)

    if target:
        if target.startswith(("http://", "https://")):
            folder = ArtifactCache(root=cache_root, ttl_seconds=ttl).paths_for_url(
                target, render_js=render_js
            )["folder"]
        else:
            folder = cache_root / target
        entries = [_entry_from_folder(folder, ttl_seconds=ttl)] if folder.exists() else []
    else:
        entries = _iter_entries(cache_root, ttl_seconds=ttl)
        if domain:
            entries = _select_entries(entries, domain=domain)
        elif expired:
            entries = _select_entries(entries, expired_only=True)

    if not entries:
        console.print("[yellow]No matching cache entries.[/]")
        return

    bulk = domain or expired or all_entries
    if bulk and not yes and not dry_run:
        console.print(f"[red]Refusing to purge {len(entries)} entries without --yes.[/]")
        console.print("[dim]Use --dry-run to inspect matches first.[/]")
        raise typer.Exit(1)

    for entry in entries:
        console.print(f"[dim]{'would purge' if dry_run else 'purging'}[/] {entry.key} {entry.url or ''}")
        if not dry_run:
            shutil.rmtree(entry.folder, ignore_errors=True)

    action = "Would purge" if dry_run else "Purged"
    console.print(f"[green]{action} {len(entries)} cache entr{'y' if len(entries) == 1 else 'ies'}.[/]")


def register(app: typer.Typer) -> None:
    app.add_typer(cache_app, name="cache")
