from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import ClassVar

from .models import ProviderCapability, ScrapeRequest, ScrapeResult

REMAINING_COST_METADATA_KEY = "_remaining_cost_units"
MAX_COST_METADATA_KEY = "_max_cost_per_url"
SPENT_COST_METADATA_KEY = "_spent_cost_units"


# The router fills every request with a generated browser identity before any
# adapter runs, and no caller code path sets headers at all, so `request.headers`
# is in practice always these and only these. An adapter that hands them to a
# remote scraping API is overriding that API's own fingerprint with a fabricated
# one it never agreed to — and ScrapeDrive was observed joining rather than
# replacing, so the target saw two user agents on one line. Forward what a caller
# could actually mean, never these.
ROUTER_BROWSER_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "accept-language",
        "cache-control",
        "priority",
        "referer",
        "sec-ch-ua",
        "sec-ch-ua-mobile",
        "sec-ch-ua-platform",
        "sec-fetch-dest",
        "sec-fetch-mode",
        "sec-fetch-site",
        "sec-fetch-user",
        "upgrade-insecure-requests",
        "user-agent",
    }
)


def caller_headers(headers: dict[str, str]) -> dict[str, str]:
    """Only the headers a caller could have meant, never the router's identity."""
    return {
        name: value for name, value in headers.items() if name.lower() not in ROUTER_BROWSER_HEADERS
    }


class ProviderAdapter(ABC):
    name: str
    cost_rank: int = 100
    capabilities: frozenset[ProviderCapability] = frozenset({"html"})
    # ClassVar because every adapter shares this one list object. Without it, an adapter
    # that appends instead of assigning would add its dependency to every other provider.
    install_requires: ClassVar[list[str]] = []
    required_configuration: tuple[tuple[str, str], ...] = ()
    is_free: bool = False

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
        # Four parallel capability guards. SIM103 wants the last one inverted into the
        # return, which breaks the symmetry and makes adding a fifth capability read
        # differently from the first four.
        if request.render_js and "render_js" not in self.capabilities:
            return False
        if request.premium and "premium" not in self.capabilities:
            return False
        if request.screenshot and "screenshot" not in self.capabilities:
            return False
        if request.country and "country" not in self.capabilities:  # noqa: SIM103
            return False
        return True

    def estimated_cost_units(self, request: ScrapeRequest) -> float:
        """Return a conservative upper bound for the next provider call.

        A provider that costs nothing sets ``is_free = True`` and may inherit this
        implementation. Any provider that can spend money must override this so a
        configured ``max_cost_per_url`` can stop it before the spend happens.

        Everything else is unpriced, and this returns infinity to say so. The router
        treats an unpriced provider as unaffordable whenever a cost ceiling is set,
        because the alternative is forecasting a paid provider as free and billing the
        user for a call the ceiling existed to prevent. Without a ceiling configured
        this is never consulted, so an unpriced provider still runs normally.
        """

        if self.is_free:
            return 0.0
        return math.inf

    @abstractmethod
    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        raise NotImplementedError
