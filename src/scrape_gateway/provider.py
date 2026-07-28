from __future__ import annotations

from abc import ABC, abstractmethod

from .models import ProviderCapability, ScrapeRequest, ScrapeResult

REMAINING_COST_METADATA_KEY = "_remaining_cost_units"
MAX_COST_METADATA_KEY = "_max_cost_per_url"
SPENT_COST_METADATA_KEY = "_spent_cost_units"


class ProviderAdapter(ABC):
    name: str
    cost_rank: int = 100
    capabilities: frozenset[ProviderCapability] = frozenset({"html"})
    install_requires: list[str] = []
    required_configuration: tuple[tuple[str, str], ...] = ()

    def availability_error(self) -> str | None:
        missing = [
            setting_name
            for attribute_name, setting_name in self.required_configuration
            if not getattr(self, attribute_name, None)
        ]
        if not missing:
            return None
        return f"Missing {' or '.join(missing)}"

    def can_handle(self, request: ScrapeRequest) -> bool:
        if request.render_js and "render_js" not in self.capabilities:
            return False
        if request.premium and "premium" not in self.capabilities:
            return False
        if request.screenshot and "screenshot" not in self.capabilities:
            return False
        return True

    def estimated_cost_units(self, request: ScrapeRequest) -> float:
        """Return a conservative upper bound for the next provider call.

        Providers that leave ``ScrapeResult.cost_units`` at its zero default may inherit
        this implementation. Paid adapters must override it so a configured gateway
        budget can stop before spend begins.
        """

        return 0.0

    @abstractmethod
    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        raise NotImplementedError
