from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from markdownify import markdownify as md

from .models import ScrapeResult


class ArtifactCache:
    def __init__(
        self, root: str | Path = ".scrape-gateway/artifacts", ttl_seconds: int = 86400
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds

    def key_for_url(self, url: str, render_js: bool = False) -> str:
        raw = f"{url}|js={render_js}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def paths_for_url(self, url: str, render_js: bool = False) -> dict[str, Path]:
        key = self.key_for_url(url, render_js=render_js)
        folder = self.root / key
        return {
            "folder": folder,
            "html": folder / "page.html",
            "markdown": folder / "page.md",
            "meta": folder / "meta.json",
            "screenshot": folder / "screenshot.bin",
        }

    def get_html(self, url: str, render_js: bool = False) -> str | None:
        paths = self.paths_for_url(url, render_js=render_js)
        html_path = paths["html"]
        meta_path = paths["meta"]
        if not html_path.exists():
            return None
        if self.ttl_seconds > 0 and meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                fetched_at = meta.get("fetched_at", 0)
                if time.time() - fetched_at > self.ttl_seconds:
                    return None
            except (json.JSONDecodeError, ValueError):
                pass
        return html_path.read_text(encoding="utf-8")

    def get_result(
        self,
        url: str,
        render_js: bool = False,
        *,
        require_screenshot: bool = False,
    ) -> ScrapeResult | None:
        """Restore a complete cached result without mixing artifact generations."""

        html = self.get_html(url, render_js=render_js)
        if html is None:
            return None

        paths = self.paths_for_url(url, render_js=render_js)
        screenshot = paths["screenshot"].read_bytes() if paths["screenshot"].exists() else None
        if require_screenshot and not screenshot:
            return None

        if paths["markdown"].exists():
            markdown = paths["markdown"].read_text(encoding="utf-8")
        else:
            markdown = md(html)

        source: dict = {}
        if paths["meta"].exists():
            try:
                source = json.loads(paths["meta"].read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                source = {}

        return ScrapeResult(
            url=url,
            provider="cache",
            success=True,
            html=html,
            markdown=markdown,
            screenshot=screenshot,
            cost_units=0,
            route="cache",
            metadata={
                "cache_hit": True,
                "cache_source_provider": source.get("provider"),
                "cache_source_route": source.get("route"),
            },
        )

    def save(self, result: ScrapeResult, render_js: bool = False) -> None:
        paths = self.paths_for_url(result.url, render_js=render_js)
        paths["folder"].mkdir(parents=True, exist_ok=True)
        if result.html:
            paths["html"].write_text(result.html, encoding="utf-8")
            markdown = result.markdown or md(result.html)
            paths["markdown"].write_text(markdown, encoding="utf-8")
        else:
            paths["html"].unlink(missing_ok=True)
            paths["markdown"].unlink(missing_ok=True)
        if result.screenshot:
            paths["screenshot"].write_bytes(result.screenshot)
        else:
            paths["screenshot"].unlink(missing_ok=True)
        meta = {
            "url": result.url,
            "provider": result.provider,
            "route": result.route,
            "fetched_at": time.time(),
        }
        paths["meta"].write_text(json.dumps(meta), encoding="utf-8")
