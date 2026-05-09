from __future__ import annotations

from abc import ABC, abstractmethod

from .models import ProviderCapability, ScrapeRequest, ScrapeResult


class ProviderAdapter(ABC):
    name: str
    cost_rank: int = 100
    capabilities: frozenset[ProviderCapability] = frozenset({"html"})
    install_requires: list[str] = []

    def can_handle(self, request: ScrapeRequest) -> bool:
        if request.render_js and "render_js" not in self.capabilities:
            return False
        if request.premium and "premium" not in self.capabilities:
            return False
        if request.screenshot and "screenshot" not in self.capabilities:
            return False
        return True

    @abstractmethod
    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        raise NotImplementedError
