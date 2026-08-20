from __future__ import annotations

import asyncio
import os
import sys
import time
from urllib.parse import urlparse

import httpx

from ..errors import classify_provider_failure
from ..models import AttemptLedgerEntry, FailureReason, ScrapeRequest, ScrapeResult
from ..provider import (
    MAX_COST_METADATA_KEY,
    REMAINING_COST_METADATA_KEY,
    SPENT_COST_METADATA_KEY,
    ProviderAdapter,
)

# Spec: https://api.scrapedrive.com:8443/api/v1/spec (v1.1). The sync host blocks
# until the scrape finishes; the async host is not used here.
SYNC_BASE = "https://sync.scrapedrive.com/api/v1/scrape"

# Public SGW tier vocabulary, kept as backward-compatible internal profiles. Each
# profile translates to explicit current spec fields (proxy_pool, render_js,
# proxy_country, wait_browser, wait_for, wait_ms, block_resources). The wire no
# longer carries scrape_tier; it only ever sees those concrete fields.
TIER_ORDER = ["standard", "advanced", "hyperdrive"]

# Additive credit model from the live spec: a fixed price reserved before the job
# runs. base 5 + JS 5 + residential 5 + screenshot 5.
BASE_COST = 5.0
RENDER_JS_COST = 5.0
RESIDENTIAL_COST = 5.0
SCREENSHOT_COST = 5.0

# Statuses the spec guarantees are rejected before any job is reserved, so they are
# never charged: bad credentials (401), insufficient credits (402), validation (422),
# rate limit / async backlog (429).
UNCHARGED_STATUS_CODES = frozenset({401, 402, 422, 429})

# The spec's own bounds. wait_ms is rejected above 30s, and timeout_ms is rejected
# below 10s or above the sync ceiling of 120s, so both are clamped rather than
# forwarded verbatim into a 422 that costs a whole attempt.
WAIT_MS_MAX = 30_000
TIMEOUT_MS_MIN = 10_000
TIMEOUT_MS_MAX = 120_000


def _tier_shape(request: ScrapeRequest, tier: str) -> tuple[bool, bool, bool]:
    """Return ``(residential, render_js, screenshot)`` for a tier's actual request shape.

    - standard: datacenter, JS only when the caller asked (or screenshot forces it).
    - advanced: residential (with proxy_country when the caller supplied one).
    - hyperdrive: residential browser — render_js always on, networkidle wait, full
      resources.
    - screenshot: the spec forces render_js on and resource blocking off.
    """
    residential = tier in {"advanced", "hyperdrive"}
    render_js = bool(request.render_js or request.screenshot or tier == "hyperdrive")
    screenshot = bool(request.screenshot)
    return residential, render_js, screenshot


def _shape_cost(residential: bool, render_js: bool, screenshot: bool) -> float:
    cost = BASE_COST
    if render_js or screenshot:  # screenshot forces render_js, so the addon applies once
        cost += RENDER_JS_COST
    if residential:
        cost += RESIDENTIAL_COST
    if screenshot:
        cost += SCREENSHOT_COST
    return cost


def _tier_cost(request: ScrapeRequest, tier: str) -> float:
    residential, render_js, screenshot = _tier_shape(request, tier)
    return _shape_cost(residential, render_js, screenshot)


def _remaining_cost(request: ScrapeRequest) -> float | None:
    raw = request.metadata.get(REMAINING_COST_METADATA_KEY)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


def _timeout_ms(request: ScrapeRequest) -> int:
    """Give the job the same deadline the HTTP client is holding it to.

    Without this the server keeps working — and charging — on a job the client has
    already hung up on, because its own default runs to the 120s sync ceiling.
    """
    return max(TIMEOUT_MS_MIN, min(TIMEOUT_MS_MAX, int(request.timeout_seconds * 1000)))


def _start_tier(request: ScrapeRequest) -> str:
    start_tier = request.metadata.get("start_tier", "")
    if start_tier.startswith("scrapedrive:"):
        remembered = start_tier.split(":", 1)[1]
        if remembered in TIER_ORDER:
            return remembered

    if request.premium:
        return "hyperdrive"
    if request.country:
        return "advanced"
    return "standard"


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


class ScrapeDriveProvider(ProviderAdapter):
    name = "scrapedrive"
    cost_rank = 25
    capabilities = frozenset({"html", "markdown", "country", "render_js", "premium", "screenshot"})
    required_configuration = (("api_key", "SCRAPEDRIVE_API_KEY"),)

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key or os.getenv("SCRAPEDRIVE_API_KEY")
        self.base_url = base_url or os.getenv("SCRAPEDRIVE_BASE_URL") or SYNC_BASE

    def estimated_cost_units(self, request: ScrapeRequest) -> float:
        return _tier_cost(request, _start_tier(request))

    def _build_params(self, request: ScrapeRequest, tier: str) -> dict[str, str]:
        residential, render_js, screenshot = _tier_shape(request, tier)
        params: dict[str, str] = {
            "api_key": self.api_key,
            "url": request.url,
            "render_js": "true" if render_js else "false",
            "device_type": "mobile" if request.mobile else "desktop",
            "result_type": ("page_markdown" if request.output_format == "markdown" else "html"),
            "timeout_ms": str(_timeout_ms(request)),
        }
        params["proxy_pool"] = "residential" if residential else "datacenter"
        if residential and request.country:
            params["proxy_country"] = request.country.upper()
        if render_js:
            # block_ads and block_resources are browser-only; the spec says they do
            # nothing on an HTML fetch, so they are only sent when one is running.
            # The spec defaults block_ads to true; sgw defaults it to false, so send
            # the caller's intent explicitly rather than inheriting the API default.
            params["block_ads"] = "true" if request.block_ads else "false"
            if tier == "hyperdrive":
                params["wait_browser"] = "networkidle"
            elif request.wait_event:
                params["wait_browser"] = request.wait_event
            # Screenshot forces block_resources off per the spec; hyperdrive wants the
            # full-resource capture shape. Otherwise keep the fast blocked fetch.
            params["block_resources"] = "false" if (screenshot or tier == "hyperdrive") else "true"
            if request.wait_selector:
                params["wait_for"] = request.wait_selector
            if request.extra_wait_ms:
                params["wait_ms"] = str(min(request.extra_wait_ms, WAIT_MS_MAX))
        if screenshot:
            params["screenshot"] = "true"
        return params

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        if error := self.availability_error():
            return ScrapeResult(
                request.url,
                self.name,
                False,
                error=error,
                failure_reason=FailureReason.PROVIDER_UNAVAILABLE,
            )

        start = _start_tier(request)
        tiers = TIER_ORDER[TIER_ORDER.index(start) :]
        attempted_tiers: list[str] = []
        ledger: list[AttemptLedgerEntry] = []
        provider_start = time.perf_counter()
        active_tier: str | None = None
        active_started: float | None = None
        remaining_cost = _remaining_cost(request)

        try:
            async with asyncio.timeout(request.timeout_seconds):
                last_result: ScrapeResult | None = None
                for tier in tiers:
                    tier_cost = _tier_cost(request, tier)
                    if remaining_cost is not None and tier_cost > remaining_cost + 1e-9:
                        spent_before = request.metadata.get(SPENT_COST_METADATA_KEY, 0)
                        global_spent = float(spent_before) + sum(
                            entry.cost_units for entry in ledger
                        )
                        budget_stop = {
                            "spent_cost_units": global_spent,
                            "remaining_cost_units": max(0.0, remaining_cost),
                            "next_provider": self.name,
                            "next_tier": tier,
                            "next_attempt_cost_units": float(tier_cost),
                        }
                        max_cost = request.metadata.get(MAX_COST_METADATA_KEY)
                        if isinstance(max_cost, (int, float)) and not isinstance(max_cost, bool):
                            budget_stop["max_cost_per_url"] = float(max_cost)
                        _log(
                            f"    [{self.name}] cost budget stops before {tier} "
                            f"({tier_cost} units; {remaining_cost:g} remaining)"
                        )
                        return ScrapeResult(
                            url=request.url,
                            provider=self.name,
                            success=False,
                            error=(
                                f"Cost budget exhausted before ScrapeDrive {tier}; "
                                f"{remaining_cost:g} units remain and {tier_cost:g} are required."
                            ),
                            failure_reason=FailureReason.BUDGET_EXCEEDED,
                            cost_units=sum(entry.cost_units for entry in ledger),
                            latency_ms=int((time.perf_counter() - provider_start) * 1000),
                            route=ledger[-1].route if ledger else None,
                            metadata={
                                "attempted_tiers": attempted_tiers,
                                "budget_stop": budget_stop,
                            },
                            attempt_ledger=list(ledger),
                        )
                    attempted_tiers.append(tier)
                    active_tier = tier
                    active_started = time.perf_counter()
                    result = await self._attempt(request, tier)
                    attempt_latency_ms = int((time.perf_counter() - active_started) * 1000)
                    # Rejected requests are never charged; everything else costs the
                    # full profile shape, estimated because responses never report the
                    # actual reserved credits.
                    charged = result.status_code not in UNCHARGED_STATUS_CODES
                    ledger_cost = 0.0 if not charged else tier_cost
                    ledger.append(
                        AttemptLedgerEntry(
                            provider=self.name,
                            route=f"scrapedrive:{tier}",
                            cost_units=ledger_cost,
                            cost_provenance="estimated",
                            success=result.success,
                            latency_ms=(
                                result.latency_ms
                                if result.latency_ms is not None
                                else attempt_latency_ms
                            ),
                            status_code=result.status_code,
                            failure_reason=result.failure_reason,
                            block_type=result.block_type,
                        )
                    )
                    result.attempt_ledger = list(ledger)
                    result.cost_units = result.run_cost_units
                    result.metadata["attempted_tiers"] = list(attempted_tiers)
                    if remaining_cost is not None:
                        remaining_cost = max(0.0, remaining_cost - ledger_cost)
                    if result.success:
                        return result
                    last_result = result
                    if tier != tiers[-1]:
                        _log(
                            f"    [{self.name}] {tier} failed, escalating to "
                            f"{tiers[tiers.index(tier) + 1]}"
                        )

                return last_result  # type: ignore[return-value]
        except TimeoutError:
            if active_tier is not None and len(ledger) < len(attempted_tiers):
                ledger.append(
                    AttemptLedgerEntry(
                        provider=self.name,
                        route=f"scrapedrive:{active_tier}",
                        cost_units=_tier_cost(request, active_tier),
                        cost_provenance="estimated",
                        success=False,
                        latency_ms=(
                            int((time.perf_counter() - active_started) * 1000)
                            if active_started is not None
                            else None
                        ),
                        status_code=None,
                        failure_reason=FailureReason.TIMEOUT,
                        block_type=None,
                    )
                )
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                error=(
                    f"ScrapeDrive exceeded its {request.timeout_seconds:g}s total timeout budget"
                ),
                failure_reason=FailureReason.TIMEOUT,
                cost_units=sum(entry.cost_units for entry in ledger),
                latency_ms=int((time.perf_counter() - provider_start) * 1000),
                route=f"scrapedrive:{active_tier}" if active_tier else None,
                metadata={
                    "attempted_tiers": attempted_tiers,
                    "timeout_seconds": request.timeout_seconds,
                },
                attempt_ledger=ledger,
            )

    async def _attempt(self, request: ScrapeRequest, tier: str) -> ScrapeResult:
        params = self._build_params(request, tier)
        shape_cost = _tier_cost(request, tier)
        timeout = request.timeout_seconds
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(self.base_url, params=params)

            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                data = response.json()
                html = data.get("html") or data.get("body") or data.get("content", "")
                markdown = data.get("markdown")
            else:
                # A successful sync scrape returns the body itself and replays the
                # target's own content-type, so page_markdown arrives labelled
                # text/html. What was asked for is the only way to know what came back.
                html = response.text
                data = None
                markdown = (
                    response.text
                    if request.output_format == "markdown" and response.is_success
                    else None
                )

            screenshot_url = response.headers.get("x-sdrive-screenshot-url") or (
                data.get("screenshot_url") if isinstance(data, dict) else None
            )

            screenshot = None
            screenshot_error = None
            if request.screenshot:
                parsed_screenshot_url = urlparse(screenshot_url or "")
                if parsed_screenshot_url.scheme not in {"http", "https"}:
                    screenshot_error = (
                        "Screenshot was requested but no downloadable URL was returned"
                    )
                else:
                    async with httpx.AsyncClient(
                        timeout=request.timeout_seconds, follow_redirects=True
                    ) as screenshot_client:
                        screenshot_response = await screenshot_client.get(screenshot_url)
                    screenshot_content_type = screenshot_response.headers.get("content-type", "")
                    if (
                        screenshot_response.is_success
                        and screenshot_response.content
                        and screenshot_content_type.lower().startswith("image/")
                    ):
                        screenshot = screenshot_response.content
                    else:
                        screenshot_error = (
                            "Screenshot download failed with HTTP "
                            f"{screenshot_response.status_code} "
                            f"({screenshot_content_type or 'unknown type'})"
                        )

            failure = classify_provider_failure(
                response.status_code,
                html if response.is_success else response.text,
            )
            if request.screenshot and not screenshot and failure is None:
                failure = FailureReason.PROVIDER_ERROR
            charged = response.status_code not in UNCHARGED_STATUS_CODES
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=response.is_success and failure is None and not screenshot_error,
                status_code=response.status_code,
                html=html,
                markdown=markdown,
                screenshot=screenshot,
                failure_reason=failure,
                error=screenshot_error,
                cost_units=0.0 if not charged else shape_cost,
                latency_ms=int((time.perf_counter() - start) * 1000),
                route=f"scrapedrive:{tier}",
                metadata={
                    "tier": tier,
                    "charged": charged,
                    "cost_provenance": "estimated",
                    # The only handle ScrapeDrive support can trace a job by.
                    "job_id": response.headers.get("x-sdrive-job-id"),
                    "screenshot_url": screenshot_url,
                    "screenshot_bytes": len(screenshot or b""),
                    **({"raw_json": data} if isinstance(data, dict) else {}),
                },
            )
        except httpx.TimeoutException as exc:
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                error=str(exc),
                failure_reason=FailureReason.TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                error=str(exc),
                failure_reason=FailureReason.PROVIDER_ERROR,
            )
