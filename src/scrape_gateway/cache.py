from __future__ import annotations

import hashlib
from pathlib import Path

from markdownify import markdownify as md

from .models import ScrapeResult


class ArtifactCache:
    def __init__(self, root: str | Path = ".scrape-gateway/artifacts") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def key_for_url(self, url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]

    def paths_for_url(self, url: str) -> dict[str, Path]:
        key = self.key_for_url(url)
        folder = self.root / key
        return {
            "folder": folder,
            "html": folder / "page.html",
            "markdown": folder / "page.md",
            "meta": folder / "meta.json",
            "screenshot": folder / "screenshot.bin",
        }

    def get_html(self, url: str) -> str | None:
        path = self.paths_for_url(url)["html"]
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def save(self, result: ScrapeResult) -> None:
        paths = self.paths_for_url(result.url)
        paths["folder"].mkdir(parents=True, exist_ok=True)
        if result.html:
            paths["html"].write_text(result.html, encoding="utf-8")
            markdown = result.markdown or md(result.html)
            paths["markdown"].write_text(markdown, encoding="utf-8")
        if result.screenshot:
            paths["screenshot"].write_bytes(result.screenshot)
