from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Literal
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

# The async host takes a job and hands back an id to poll. It is the only way to
# run anything past the 120s sync ceiling, which a browser job on a hard site
# regularly needs.
ASYNC_BASE = "https://api.scrapedrive.com:8443/api/v1/scrape/async"

# Public SGW tier vocabulary, kept as backward-compatible internal profiles. Each
# profile translates to explicit current spec fields (proxy_pool, render_js,
# proxy_country, wait_browser, wait_for, wait_ms, block_resources). The wire no
# longer carries scrape_tier; it only ever sees those concrete fields.
TIER_ORDER = ["standard", "advanced", "hyperdrive"]

# ScrapeDrive's own progressive escalation, opt-in via SCRAPEDRIVE_AUTO. It replaces
# the whole ladder with one call: the job starts at its cheapest compatible setting,
# advances only after failure, and is charged once for the configuration that
# succeeded. Internal failed attempts are not charged again.
AUTO_TIER = "auto"

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

# The browser identity ScrapeDrive builds for itself. Ours must not be forwarded
# on top of it: the values are joined rather than replaced, so the target sees
# both. Referer is here because the router synthesises one into every request;
# a referer the caller actually chose arrives as its own field instead.
FINGERPRINT_HEADERS = frozenset(
    {
        "user-agent",
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
    }
)

# The spec's own bounds. wait_ms is rejected above 30s, and timeout_ms is rejected
# below 10s or above the sync ceiling of 120s, so both are clamped rather than
# forwarded verbatim into a 422 that costs a whole attempt.
WAIT_MS_MAX = 30_000
TIMEOUT_MS_MIN = 10_000
TIMEOUT_MS_MAX = 120_000
ASYNC_TIMEOUT_MS_MAX = 130_000

# How long each submit or poll call may take. Not the job's deadline — that is
# the caller's timeout, enforced around the whole ladder.
JOB_HTTP_TIMEOUT_SECONDS = 30.0
POLL_INTERVAL_SECONDS = 2.0

# The status word cannot be used to decide a job is finished. "queued",
# "processing" and "active" have all been seen on a job still running, so any
# allow-list of in-flight words is a guess that ends in an empty result the
# moment a fourth one appears — which is exactly how this was found. The result
# payload is the dependable marker, and these are the words that mean a job
# ended without ever producing one.
FAILED_STATUSES = frozenset({"failed", "error", "cancelled", "canceled", "expired", "timeout"})

# A screenshot URL is handed back before the object store has the file, so the first
# download can 403 on a key that does not exist yet.
SCREENSHOT_DOWNLOAD_ATTEMPTS = 3
SCREENSHOT_RETRY_DELAY_SECONDS = 1.0


def _tier_shape(request: ScrapeRequest, tier: str) -> tuple[bool, bool, bool]:
    """Return ``(residential, render_js, screenshot)`` for a tier's actual request shape.

    - standard: datacenter, JS only when the caller asked (or screenshot forces it).
    - advanced: residential (with proxy_country when the caller supplied one).
    - hyperdrive: residential browser — render_js always on, networkidle wait, full
      resources.
    - auto: ScrapeDrive picks the proxy, so nothing here asks for residential; the
      caller's own render_js and screenshot still act as the floor it starts from.
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
    if tier == AUTO_TIER:
        return _auto_max_credits(request)
    residential, render_js, screenshot = _tier_shape(request, tier)
    return _shape_cost(residential, render_js, screenshot)


def _remaining_cost(request: ScrapeRequest) -> float | None:
    raw = request.metadata.get(REMAINING_COST_METADATA_KEY)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


def _auto_floor_cost(request: ScrapeRequest) -> float:
    """The cheapest configuration an Auto job is allowed to start from.

    Auto starts cheap and escalates, but it cannot go below what the caller demanded:
    asking for JavaScript or a screenshot already rules the datacenter HTML fetch out,
    and a max_credits under that floor is rejected as unsatisfiable.
    """
    wants_browser = bool(request.render_js or request.screenshot)
    return _shape_cost(False, wants_browser, bool(request.screenshot))


def _auto_ceiling_cost(request: ScrapeRequest) -> float:
    """The most an Auto job may reach — the same shape the manual ladder tops out at."""
    return _shape_cost(True, True, bool(request.screenshot))


def _auto_max_credits(request: ScrapeRequest) -> float:
    """Resolve the ``max_credits`` ceiling to reserve for an Auto job.

    This doubles as the cost estimate, because max_credits is exactly the bound on what
    the job can charge. A remaining budget below the floor returns the floor, so the
    caller's affordability check refuses the provider instead of sending a request the
    API would reject anyway.
    """
    floor = _auto_floor_cost(request)
    ceiling = _auto_ceiling_cost(request)
    remaining = _remaining_cost(request)
    if remaining is None:
        return ceiling
    # max_credits is an integer, so a fractional remainder rounds down rather than
    # reserving a credit the budget does not cover.
    affordable = float(int(min(ceiling, remaining)))
    return max(affordable, floor)


def _timeout_ms(request: ScrapeRequest, ceiling: int = TIMEOUT_MS_MAX) -> int:
    """Give the job the same deadline the HTTP client is holding it to.

    Without this the server keeps working — and charging — on a job the client has
    already hung up on, because its own default runs to the mode's ceiling.
    """
    return max(TIMEOUT_MS_MIN, min(ceiling, int(request.timeout_seconds * 1000)))


def _uses_async(request: ScrapeRequest) -> bool:
    """Whether this request has to go to the async host.

    Sync cannot be held open past 120s, so a caller asking for longer would have
    its job killed at the ceiling no matter what it waited for. The spec's own
    advice is to use async for those.
    """
    return request.timeout_seconds * 1000 > TIMEOUT_MS_MAX


def _forwarded_headers(request: ScrapeRequest) -> dict[str, str]:
    """Caller headers, renamed to the prefix ScrapeDrive forwards to the target.

    Anything sent as `sdrive-X` arrives at the target as `X`, so this carries the
    things ScrapeDrive cannot invent — authorization, cookies, custom fields.
    Without it the adapter accepted a headers dict and silently dropped it.

    It deliberately does not carry the browser identity. The router fills every
    request with a generated User-Agent, Accept, Sec-Fetch-* set before any
    adapter sees it, and forwarding those does not replace ScrapeDrive's own —
    the two values are joined into one header. A target then receives two user
    agents on a single line, one claiming Chrome 131 and one Chrome 140, while
    Sec-Ch-Ua describes only the second. That is a louder bot signal than
    sending nothing, and it would have fired on every request. Confirmed against
    a header echo service on 2026-08-20.
    """
    headers = {
        f"sdrive-{name}": value
        for name, value in request.headers.items()
        if name.lower() not in FINGERPRINT_HEADERS
    }
    # referer is its own field on the request, so a value here is the caller's
    # own and not the one the router synthesised into the headers dict. None
    # means "let the provider decide" and "" means "send none".
    if request.referer:
        headers["sdrive-Referer"] = request.referer
    return headers


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


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


class ScrapeDriveProvider(ProviderAdapter):
    name = "scrapedrive"
    cost_rank = 25
    capabilities = frozenset({"html", "markdown", "country", "render_js", "premium", "screenshot"})
    required_configuration = (("api_key", "SCRAPEDRIVE_API_KEY"),)

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        auto: bool | None = None,
        async_url: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("SCRAPEDRIVE_API_KEY")
        self.base_url = base_url or os.getenv("SCRAPEDRIVE_BASE_URL") or SYNC_BASE
        self.async_url = async_url or os.getenv("SCRAPEDRIVE_ASYNC_URL") or ASYNC_BASE
        self.auto = _env_flag("SCRAPEDRIVE_AUTO") if auto is None else auto

    def _uses_auto(self, request: ScrapeRequest) -> bool:
        """Whether this request may be handed to ScrapeDrive's own escalation.

        Auto refuses every caller-supplied routing field, so a request that names a
        country has to stay on the manual ladder: proxy_country is only accepted in
        standard mode on a residential pool.
        """
        return bool(self.auto) and not request.country

    def estimated_cost_units(self, request: ScrapeRequest) -> float:
        if self._uses_auto(request):
            return _auto_max_credits(request)
        return _tier_cost(request, _start_tier(request))

    def _job_fields(self, request: ScrapeRequest, tier: str) -> dict[str, object]:
        """The job description, in native types, before it is encoded for a mode."""
        residential, render_js, screenshot = _tier_shape(request, tier)
        ceiling = ASYNC_TIMEOUT_MS_MAX if _uses_async(request) else TIMEOUT_MS_MAX
        fields: dict[str, object] = {
            "url": request.url,
            "render_js": render_js,
            "device_type": "mobile" if request.mobile else "desktop",
            "result_type": ("page_markdown" if request.output_format == "markdown" else "html"),
            "timeout_ms": _timeout_ms(request, ceiling),
        }
        if tier == AUTO_TIER:
            fields["auto"] = True
            fields["max_credits"] = int(_auto_max_credits(request))
        else:
            fields["proxy_pool"] = "residential" if residential else "datacenter"
            if residential and request.country:
                fields["proxy_country"] = request.country.upper()
        if render_js:
            # block_ads and block_resources are browser-only; the spec says they do
            # nothing on an HTML fetch, so they are only sent when one is running.
            # The spec defaults block_ads to true; sgw defaults it to false, so send
            # the caller's intent explicitly rather than inheriting the API default.
            fields["block_ads"] = bool(request.block_ads)
            if tier == "hyperdrive":
                fields["wait_browser"] = "networkidle"
            elif request.wait_event:
                fields["wait_browser"] = request.wait_event
            # Screenshot forces block_resources off per the spec; hyperdrive wants the
            # full-resource capture shape. Otherwise keep the fast blocked fetch.
            fields["block_resources"] = not (screenshot or tier == "hyperdrive")
            if request.wait_selector:
                fields["wait_for"] = request.wait_selector
            if request.extra_wait_ms:
                fields["wait_ms"] = min(request.extra_wait_ms, WAIT_MS_MAX)
        if screenshot:
            fields["screenshot"] = True
        if _forwarded_headers(request):
            # Only when something survives the fingerprint filter. Asking for
            # forwarding with nothing to forward advertises an intent the request
            # does not have.
            fields["forward_sdrive_headers"] = True
        if not _uses_async(request):
            # Without this a blocked page comes back as ScrapeDrive's own JSON error
            # with HTTP 500, so every block is diagnosed as "the provider broke".
            # With it the target's real status and body arrive — a 403 and the
            # challenge page — which is what the classifier, the block-signature
            # table and the evidence feed all need. The cost is ScrapeDrive's
            # friendly `reason` string, which the body replaces. Sync only: the
            # spec restricts it to that mode.
            fields["transparent_mode"] = True
        return fields

    def _build_params(self, request: ScrapeRequest, tier: str) -> dict[str, str]:
        """Query-string encoding, for the sync host."""
        params: dict[str, str] = {"api_key": self.api_key}
        for name, value in self._job_fields(request, tier).items():
            params[name] = ("true" if value else "false") if isinstance(value, bool) else str(value)
        return params

    def _build_payload(self, request: ScrapeRequest, tier: str) -> dict[str, object]:
        """JSON-body encoding, for the async host.

        The async host rejects an Auto job whose max_credits arrives as a query
        string — same value, same spelling, a 500 saying it needs a positive
        max_credits. Sent as a JSON number in the body it is accepted, so async
        submits are always POSTs.
        """
        return {"api_key": self.api_key, **self._job_fields(request, tier)}

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        if error := self.availability_error():
            return ScrapeResult(
                request.url,
                self.name,
                False,
                error=error,
                failure_reason=FailureReason.PROVIDER_UNAVAILABLE,
            )

        dropped = _forwarded_headers(request) if not _uses_async(request) else {}
        if dropped:
            # Verified 2026-08-20: the async host strips the sdrive- prefix and
            # passes the header on, the sync host silently does not. Both are sent
            # either way, so this stops being a caveat the day sync catches up —
            # but until then a dropped Authorization header must not be silent.
            _log(
                f"    [{self.name}] the sync host does not forward sdrive- headers, so "
                f"{len(dropped)} caller header(s) will not reach the target "
                f"({', '.join(sorted(name[len('sdrive-') :] for name in dropped))}); "
                f"a timeout above {TIMEOUT_MS_MAX // 1000}s routes the job to the async "
                f"host, which does forward them"
            )
        if self._uses_auto(request):
            tiers = [AUTO_TIER]
        else:
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
                    # An async job states the credits it was charged, so that figure
                    # is a bill. A sync response never does, so a rejected request
                    # costs nothing and everything else costs the full profile shape.
                    if result.metadata.get("cost_provenance") == "exact":
                        ledger_cost = result.cost_units
                        provenance: Literal["exact", "estimated"] = "exact"
                    else:
                        charged = (
                            result.metadata.get("charged") is not False
                            and result.status_code not in UNCHARGED_STATUS_CODES
                        )
                        ledger_cost = tier_cost if charged else 0.0
                        provenance = "estimated"
                    ledger.append(
                        AttemptLedgerEntry(
                            provider=self.name,
                            route=f"scrapedrive:{tier}",
                            cost_units=ledger_cost,
                            cost_provenance=provenance,
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

    async def _download_screenshot(
        self, screenshot_url: str, timeout_seconds: float
    ) -> tuple[bytes | None, str | None]:
        """Fetch the capture the job pointed at, retrying while it is still landing.

        The URL comes back before the object store has finished writing it, and the
        bucket answers 403 for a key that is not there yet. Without the retry that
        reads as a failed screenshot, which fails the attempt and escalates the whole
        ladder — 55 credits spent on an image that was readable a second later.
        """
        last_error: str | None = None
        for attempt in range(SCREENSHOT_DOWNLOAD_ATTEMPTS):
            if attempt:
                await asyncio.sleep(SCREENSHOT_RETRY_DELAY_SECONDS)
            async with httpx.AsyncClient(
                timeout=timeout_seconds, follow_redirects=True
            ) as screenshot_client:
                response = await screenshot_client.get(screenshot_url)
            content_type = response.headers.get("content-type", "")
            if (
                response.is_success
                and response.content
                and content_type.lower().startswith("image/")
            ):
                return response.content, None
            last_error = (
                "Screenshot download failed with HTTP "
                f"{response.status_code} ({content_type or 'unknown type'})"
            )
        return None, last_error

    async def _attempt(self, request: ScrapeRequest, tier: str) -> ScrapeResult:
        if _uses_async(request):
            return await self._attempt_async(request, tier)
        return await self._attempt_sync(request, tier)

    async def _attempt_async(self, request: ScrapeRequest, tier: str) -> ScrapeResult:
        """Submit the job, poll until it settles, and report what it really cost.

        Unlike sync, the finished job states the credits it was charged, so this
        path reports exact cost rather than the shape estimate.
        """
        shape_cost = _tier_cost(request, tier)
        start = time.perf_counter()

        def failed(error: str, reason: FailureReason, status: int | None = None) -> ScrapeResult:
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                status_code=status,
                error=error,
                failure_reason=reason,
                cost_units=0.0,
                latency_ms=int((time.perf_counter() - start) * 1000),
                route=f"scrapedrive:{tier}",
                metadata={"tier": tier, "mode": "async", "charged": False},
            )

        try:
            async with httpx.AsyncClient(
                timeout=JOB_HTTP_TIMEOUT_SECONDS, follow_redirects=True
            ) as client:
                submit = await client.post(
                    self.async_url,
                    json=self._build_payload(request, tier),
                    headers=_forwarded_headers(request) or None,
                )
                if not submit.is_success:
                    return failed(
                        f"ScrapeDrive refused the async job: HTTP {submit.status_code} "
                        f"{submit.text.strip()[:200]}",
                        classify_provider_failure(submit.status_code, submit.text)
                        or FailureReason.PROVIDER_ERROR,
                        submit.status_code,
                    )
                envelope = submit.json()
                # The submit response calls it "id"; the spec's prose calls it
                # job_id. Read both rather than trusting either.
                job_id = envelope.get("id") or envelope.get("job_id")
                status_url = envelope.get("status_url")
                if not status_url and job_id:
                    status_url = f"{self.async_url.rsplit('/scrape/async', 1)[0]}/job/{job_id}"
                if not status_url:
                    return failed(
                        "ScrapeDrive accepted the async job but returned no way to poll it",
                        FailureReason.PROVIDER_ERROR,
                        submit.status_code,
                    )

                while True:
                    poll = await client.get(status_url)
                    if not poll.is_success:
                        return failed(
                            f"Polling the ScrapeDrive job failed: HTTP {poll.status_code}",
                            FailureReason.PROVIDER_ERROR,
                            poll.status_code,
                        )
                    job = poll.json()
                    if job.get("response") or str(job.get("status", "")).lower() in FAILED_STATUSES:
                        break
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
        except httpx.TimeoutException as exc:
            return failed(str(exc), FailureReason.TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            return failed(str(exc), FailureReason.PROVIDER_ERROR)

        return await self._async_outcome(request, tier, job, job_id, shape_cost, start)

    async def _async_outcome(
        self,
        request: ScrapeRequest,
        tier: str,
        job: dict,
        job_id: str | None,
        shape_cost: float,
        start: float,
    ) -> ScrapeResult:
        """Turn a settled job into a result.

        `status` is "completed" even for a scrape that never reached the target,
        so it says nothing about success. The signal is inside `response`: a
        status_code of 0 with an empty body and a `reason` at the top level.
        """
        payload = job.get("response") or {}
        body = payload.get("body") or ""
        target_status = payload.get("status_code") or None
        reason = (job.get("reason") or "").strip()
        credits = payload.get("credits")
        exact_cost = isinstance(credits, (int, float)) and not isinstance(credits, bool)
        headers = {
            str(name).lower(): value for name, value in (payload.get("headers") or {}).items()
        }
        markdown = body if request.output_format == "markdown" and target_status else None

        screenshot = None
        screenshot_error = None
        if request.screenshot:
            screenshot_url = headers.get("x-sdrive-screenshot-url") or payload.get("screenshot_url")
            if not screenshot_url:
                screenshot_error = "Screenshot was requested but no downloadable URL was returned"
            else:
                screenshot, screenshot_error = await self._download_screenshot(
                    screenshot_url, JOB_HTTP_TIMEOUT_SECONDS
                )

        if not target_status:
            if not reason:
                reason = (
                    f"ScrapeDrive job {job_id} ended as "
                    f"{job.get('status') or 'unknown'} without a result"
                )
            failure = FailureReason.PROXY_ERROR
        else:
            failure = classify_provider_failure(target_status, body)
        if request.screenshot and not screenshot and failure is None:
            failure = FailureReason.PROVIDER_ERROR

        return ScrapeResult(
            url=request.url,
            provider=self.name,
            success=bool(target_status) and failure is None and not screenshot_error,
            status_code=target_status,
            html=body or None,
            markdown=markdown,
            screenshot=screenshot,
            failure_reason=failure,
            error=screenshot_error or reason or None,
            cost_units=float(credits) if exact_cost else shape_cost,
            latency_ms=int((time.perf_counter() - start) * 1000),
            route=f"scrapedrive:{tier}",
            metadata={
                "tier": tier,
                "mode": "async",
                "charged": bool(credits) if exact_cost else True,
                # The finished job states its own price, so this is a bill and
                # not a forecast — the only ScrapeDrive path where that is true.
                "cost_provenance": "exact" if exact_cost else "estimated",
                "job_id": job_id,
                "final_url": payload.get("final_url"),
                "screenshot_bytes": len(screenshot or b""),
            },
        )

    async def _attempt_sync(self, request: ScrapeRequest, tier: str) -> ScrapeResult:
        params = self._build_params(request, tier)
        shape_cost = _tier_cost(request, tier)
        timeout = request.timeout_seconds
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(
                    self.base_url, params=params, headers=_forwarded_headers(request) or None
                )

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
                    screenshot, screenshot_error = await self._download_screenshot(
                        screenshot_url, request.timeout_seconds
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
                    **({"max_credits": int(shape_cost)} if tier == AUTO_TIER else {}),
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
