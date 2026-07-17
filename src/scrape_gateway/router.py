from __future__ import annotations

import json
import logging
import os
import random
import re
import sys
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse

from markdownify import markdownify as md

from .cache import ArtifactCache
from .config import GatewayConfig, StrategyConfig, load_config
from .memory import DomainMemory
from .models import FailureReason, ScrapeRequest, ScrapeResult
from .provider import ProviderAdapter
from .progress import emit_progress
from .telemetry import TelemetryRecorder, new_run_id, safe_metadata, utc_now
from .validators import validate_content

LOG_DIR = Path(".scrape-gateway")
LOG_FILE = LOG_DIR / "scrape.log"

logger = logging.getLogger("scrape_gateway")


def _init_logger() -> None:
    if logger.handlers:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _log_event(event: str, **data) -> None:
    _init_logger()
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **data}
    logger.info(json.dumps(entry, default=str))


CCTLD_TO_COUNTRY = {
    "ro": "RO",
    "de": "DE",
    "fr": "FR",
    "es": "ES",
    "it": "IT",
    "nl": "NL",
    "pt": "PT",
    "pl": "PL",
    "cz": "CZ",
    "at": "AT",
    "ch": "CH",
    "be": "BE",
    "se": "SE",
    "no": "NO",
    "dk": "DK",
    "fi": "FI",
    "ie": "IE",
    "hu": "HU",
    "bg": "BG",
    "hr": "HR",
    "sk": "SK",
    "si": "SI",
    "lt": "LT",
    "lv": "LV",
    "ee": "EE",
    "gr": "GR",
    "cy": "CY",
    "mt": "MT",
    "lu": "LU",
    "uk": "GB",
    "co.uk": "GB",
    "jp": "JP",
    "kr": "KR",
    "cn": "CN",
    "in": "IN",
    "br": "BR",
    "mx": "MX",
    "ar": "AR",
    "cl": "CL",
    "au": "AU",
    "nz": "NZ",
    "ca": "CA",
    "za": "ZA",
    "ru": "RU",
    "tr": "TR",
    "ua": "UA",
    "il": "IL",
    "ae": "AE",
    "sg": "SG",
    "th": "TH",
    "vn": "VN",
    "my": "MY",
    "ph": "PH",
    "id": "ID",
    "tw": "TW",
    "hk": "HK",
}

_HREFLANG_RE = re.compile(
    r'<link[^>]+hreflang=["\']([^"\']+)["\'][^>]+href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _country_from_tld(url: str) -> str | None:
    host = urlparse(url).hostname or ""
    parts = host.rsplit(".", 2)
    if len(parts) >= 3:
        two_part = f"{parts[-2]}.{parts[-1]}"
        if two_part in CCTLD_TO_COUNTRY:
            return CCTLD_TO_COUNTRY[two_part]
    tld = parts[-1] if parts else ""
    return CCTLD_TO_COUNTRY.get(tld)


def _check_hreflang(html: str, url: str, country: str | None) -> dict | None:
    matches = _HREFLANG_RE.findall(html)
    if not matches:
        return None
    hreflangs = {lang.lower(): href for lang, href in matches}
    result = {"hreflangs": hreflangs}
    if country:
        cc = country.lower()
        matching = [lang for lang in hreflangs if cc in lang.split("-")]
        if matching:
            result["country_match"] = True
            if hreflangs[matching[0]] != url:
                result["canonical_url"] = hreflangs[matching[0]]
        else:
            result["country_match"] = False
            available = [lang for lang in hreflangs if lang != "x-default"]
            if available:
                result["available_countries"] = available
    return result


def _default_providers() -> list[ProviderAdapter]:
    from .discovery import discover_providers

    return [cls() for cls in discover_providers().values()]


def _providers_from_config(config: GatewayConfig) -> list[ProviderAdapter]:
    if not config.providers:
        return _default_providers()

    from .discovery import discover_providers

    available = discover_providers()
    configured_names = set()
    result = []
    for pc in config.providers:
        configured_names.add(pc.name)
        if not pc.enabled:
            continue
        cls = available.get(pc.name)
        if not cls:
            _log(f"  [config] unknown provider: {pc.name}")
            continue
        result.append(cls(**pc.options))
    for name, cls in available.items():
        if name not in configured_names:
            result.append(cls())
    return result


_REFERER_POOL = [
    "https://www.google.com/",
    "https://www.google.com/search?q={domain}",
    "https://www.google.com/search?q={domain_words}",
    "https://www.bing.com/search?q={domain}",
    "https://duckduckgo.com/?q={domain}",
    "https://t.co/redirect",
    "https://www.reddit.com/",
]

_ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-US,en;q=0.9,es;q=0.8",
    "en-GB,en;q=0.9,en-US;q=0.8",
    "en-US,en;q=0.9,fr;q=0.8",
    "en-US,en;q=0.9,de;q=0.8",
    "en,en-US;q=0.9",
]

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
]


def _auto_referer(url: str) -> str:
    parsed = urlparse(url)
    domain = (parsed.hostname or "").removeprefix("www.")
    domain_words = domain.replace(".", " ").replace("-", " ").strip()
    template = random.choice(_REFERER_POOL)
    return template.format(domain=domain, domain_words=domain_words)


def _apply_browser_headers(request_headers: dict[str, str], url: str) -> None:
    """Fill in realistic browser headers that aren't already set."""
    h = request_headers
    referer = h.get("Referer", "")
    target_host = urlparse(url).hostname or ""
    referer_host = urlparse(referer).hostname or "" if referer else ""

    if referer_host and referer_host == target_host:
        fetch_site = "same-origin"
    elif referer:
        fetch_site = "cross-site"
    else:
        fetch_site = "none"

    defaults = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": fetch_site,
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": random.choice(_ACCEPT_LANGUAGES),
        "Cache-Control": "max-age=0",
        "Priority": "u=0, i",
    }
    for key, val in defaults.items():
        if key not in h and key.lower() not in {k.lower() for k in h}:
            h[key] = val


class ScrapeGateway:
    def __init__(
        self,
        providers: Iterable[ProviderAdapter] | None = None,
        cache: ArtifactCache | None = None,
        memory: DomainMemory | None = None,
        strategy: StrategyConfig | None = None,
        telemetry: TelemetryRecorder | None = None,
        evaluator=None,
        default_timeout_seconds: float = 45,
        provider_timeouts: dict[str, float] | None = None,
    ) -> None:
        self.providers = list(providers if providers is not None else _default_providers())
        self.cache = cache or ArtifactCache()
        self.memory = memory or DomainMemory()
        self.strategy = strategy or StrategyConfig()
        self.telemetry = telemetry or TelemetryRecorder()
        self.evaluator = evaluator
        self.default_timeout_seconds = default_timeout_seconds
        self.provider_timeouts = provider_timeouts or {}

    @classmethod
    def from_config(cls, config: GatewayConfig | None = None) -> ScrapeGateway:
        config = config or load_config()
        evaluator = None
        if config.evaluation.mode != "off":
            from .evaluation import OpenRouterEvaluator

            evaluator = OpenRouterEvaluator(config.evaluation)
        return cls(
            providers=_providers_from_config(config),
            cache=ArtifactCache(root=config.cache.root, ttl_seconds=config.cache.ttl_seconds),
            memory=DomainMemory(db_path=config.memory_path),
            strategy=config.strategy,
            telemetry=TelemetryRecorder(
                root=config.telemetry.root,
                enabled=config.telemetry.enabled,
                debug_artifacts=config.telemetry.debug_artifacts,
            ),
            evaluator=evaluator,
            default_timeout_seconds=config.request.default_timeout_seconds,
            provider_timeouts={
                provider.name: provider.timeout_seconds
                for provider in config.providers
                if provider.enabled and provider.timeout_seconds is not None
            },
        )

    async def _evaluate_result(
        self,
        *,
        run_id: str,
        request: ScrapeRequest,
        result: ScrapeResult,
        attempts: list[dict],
        elapsed_ms: int,
    ) -> dict | None:
        if self.evaluator is None:
            emit_progress(
                id="evaluation",
                name="AI evaluation",
                kind="evaluation",
                status="skipped",
                outcome="disabled",
                summary="AI evaluation is disabled for this gateway.",
                attributes={"screenshot_bytes": len(result.screenshot or b"")},
            )
            return None
        evaluation_start = time.perf_counter()
        emit_progress(
            id="evaluation",
            name="AI evaluation",
            kind="evaluation",
            status="running",
            outcome="judging",
            summary="Sending saved text and available visual evidence to the evaluator.",
            attributes={
                "model": getattr(getattr(self.evaluator, "config", None), "model", "unknown"),
                "markdown_chars": len(result.markdown or ""),
                "screenshot_bytes": len(result.screenshot or b""),
            },
        )
        try:
            outcome = await self.evaluator.evaluate(
                request=request,
                result=result,
                attempts=attempts,
                elapsed_ms=elapsed_ms,
            )
        except Exception as exc:  # noqa: BLE001 - audit must not break a scrape
            from .evaluation import EvaluationOutcome

            model = getattr(getattr(self.evaluator, "config", None), "model", "unknown")
            outcome = EvaluationOutcome(
                status="failed",
                model=model,
                input_modalities=["markdown"] if result.markdown is not None else [],
                markdown_evidence=result.markdown or "",
                error=f"{type(exc).__name__}: {exc}",
            )
        try:
            artifacts = self.telemetry.write_evaluation_artifacts(run_id, outcome, result)
        except Exception as exc:  # noqa: BLE001 - preserve the primary scrape result
            artifacts = {}
            outcome.response_metadata["artifact_error"] = f"{type(exc).__name__}: {exc}"
        summary = outcome.summary(artifacts)
        result.metadata["evaluation"] = {
            "status": summary["status"],
            "calibration_status": summary["calibration_status"],
            "verdict": summary["verdict"],
            "needs_human_review": summary["needs_human_review"],
            "recommended_action": summary["recommended_action"],
            "report_artifacts": artifacts,
        }
        emit_progress(
            id="evaluation",
            name="AI evaluation",
            kind="evaluation",
            status="ok" if summary["status"] == "completed" else "error",
            outcome=summary.get("verdict") or summary["status"],
            summary=(
                summary.get("critique")
                or summary.get("error")
                or f"Evaluator finished with {summary.get('verdict') or summary['status']}."
            ),
            duration_ms=int((time.perf_counter() - evaluation_start) * 1000),
            attributes={
                "model": summary.get("model"),
                "input_modalities": summary.get("input_modalities", []),
                "screenshot_bytes": len(result.screenshot or b""),
            },
        )
        return summary

    def _write_report_with_progress(self, report: dict) -> Path | None:
        persistence_start = time.perf_counter()
        emit_progress(
            id="persistence",
            name="Save run evidence",
            kind="persistence",
            status="running",
            outcome="saving",
            summary="Writing the report and captured artifacts.",
            attributes={},
        )
        report_path = self.telemetry.write_report(report)
        emit_progress(
            id="persistence",
            name="Save run evidence",
            kind="persistence",
            status="ok" if report_path else "skipped",
            outcome="saved" if report_path else "telemetry_disabled",
            summary=str(report_path) if report_path else "Telemetry persistence is disabled.",
            duration_ms=int((time.perf_counter() - persistence_start) * 1000),
            attributes={"report_path": str(report_path) if report_path else None},
        )
        return report_path

    async def scrape(
        self, request: ScrapeRequest, use_cache: bool = True, use_memory: bool = True
    ) -> ScrapeResult:
        if not request.url.startswith(("http://", "https://")):
            request.url = f"https://{request.url}"
        if "Referer" not in request.headers and "referer" not in request.headers:
            if request.referer is None:
                request.headers["Referer"] = _auto_referer(request.url)
            elif request.referer:
                request.headers["Referer"] = request.referer
        _apply_browser_headers(request.headers, request.url)
        _log(f"\nscrape {request.url}")
        scrape_start = time.perf_counter()
        explicit_timeout = request.timeout_seconds if request.timeout_seconds != 45 else None
        if explicit_timeout is None:
            request.timeout_seconds = self.default_timeout_seconds
        run_id = request.metadata.get("run_id") or new_run_id()
        request.metadata["run_id"] = run_id
        started_at = utc_now()
        proxy_enabled = bool(os.getenv("SCRAPE_PROXY_URL"))
        req_data = {
            "url": request.url,
            "country": request.country,
            "render_js": request.render_js,
            "premium": request.premium,
            "mobile": request.mobile,
            "wait_event": request.wait_event,
            "wait_selector": request.wait_selector,
            "output_format": request.output_format,
            "run_id": run_id,
        }

        if not request.country:
            detected = _country_from_tld(request.url)
            if detected:
                request.country = detected
                req_data["country"] = detected
                req_data["country_source"] = "tld"
                _log(f"  [auto-country] {detected} (from TLD)")

        _log_event("scrape_start", **req_data)

        if use_cache:
            result = self.cache.get_result(
                request.url,
                render_js=request.render_js,
                require_screenshot=request.screenshot,
            )
            if result:
                _log("  [cache] HIT")
                _log_event("cache_hit", url=request.url, run_id=run_id)
                result.metadata["run_id"] = run_id
                elapsed_ms = int((time.perf_counter() - scrape_start) * 1000)
                evaluation = await self._evaluate_result(
                    run_id=run_id,
                    request=request,
                    result=result,
                    attempts=[],
                    elapsed_ms=elapsed_ms,
                )
                report = self.telemetry.build_report(
                    run_id=run_id,
                    started_at=started_at,
                    finished_at=utc_now(),
                    elapsed_ms=elapsed_ms,
                    request=request,
                    use_cache=use_cache,
                    use_memory=use_memory,
                    proxy_enabled=proxy_enabled,
                    final=result,
                    attempts=[],
                    skipped=[],
                    evaluation=evaluation,
                )
                report_path = self._write_report_with_progress(report)
                if report_path:
                    result.metadata["telemetry_report"] = str(report_path)
                return result
            _log("  [cache] MISS")

        ordered = self._ordered_providers(request)
        emit_progress(
            id="routing",
            name="Build provider route",
            kind="routing",
            status="ok",
            outcome="planned",
            summary=f"Prepared {len(ordered)} provider{'s' if len(ordered) != 1 else ''}.",
            attributes={"providers": [provider.name for provider in ordered]},
        )
        attempts = []
        skipped = []
        last_result: ScrapeResult | None = None
        for provider_index, provider in enumerate(ordered, start=1):
            provider_step_id = f"provider-{provider_index}"
            if explicit_timeout is None:
                request.timeout_seconds = self.provider_timeouts.get(
                    provider.name, self.default_timeout_seconds
                )
            if not provider.can_handle(request):
                skipped.append(f"{provider.name}(no capability)")
                emit_progress(
                    id=provider_step_id,
                    name=f"{provider.name} attempt",
                    kind="provider",
                    status="skipped",
                    outcome="missing_capability",
                    summary="Provider cannot satisfy the requested capture options.",
                    attributes={
                        "provider": provider.name,
                        "screenshot_requested": request.screenshot,
                    },
                )
                continue
            if use_memory and self.memory.should_skip_provider(request.url, provider.name):
                skipped.append(f"{provider.name}(bad history)")
                emit_progress(
                    id=provider_step_id,
                    name=f"{provider.name} attempt",
                    kind="provider",
                    status="skipped",
                    outcome="bad_history",
                    summary="Domain memory skipped this provider after prior failures.",
                    attributes={"provider": provider.name},
                )
                continue
            start = time.perf_counter()
            emit_progress(
                id=provider_step_id,
                name=f"{provider.name} attempt",
                kind="provider",
                status="running",
                outcome="requesting",
                summary=f"Waiting for {provider.name} to return the page.",
                attributes={
                    "provider": provider.name,
                    "timeout_seconds": request.timeout_seconds,
                    "screenshot_requested": request.screenshot,
                    "render_js": request.render_js,
                },
            )
            result = await provider.scrape(request)
            elapsed = time.perf_counter() - start
            emit_progress(
                id=provider_step_id,
                name=f"{provider.name} attempt",
                kind="provider",
                status="ok" if result.success else "error",
                outcome="response_received" if result.success else "failed",
                summary=(
                    f"HTTP {result.status_code or 'unknown'}; "
                    f"{len(result.html or '')} HTML chars; "
                    f"{len(result.screenshot or b'')} screenshot bytes."
                ),
                duration_ms=int(elapsed * 1000),
                attributes={
                    "provider": provider.name,
                    "route": result.route,
                    "status": result.status_code,
                    "failure_reason": result.failure_reason.value if result.failure_reason else None,
                    "html_chars": len(result.html or ""),
                    "screenshot_bytes": len(result.screenshot or b""),
                    "screenshot_requested": request.screenshot,
                },
            )
            if request.skip_validation and not result.success and result.html:
                if result.status_code and 200 <= result.status_code < 400:
                    result.success = True
                    result.failure_reason = None
            attempt = {
                "provider": provider.name,
                "status": result.status_code,
                "elapsed_ms": int(elapsed * 1000),
                "route": result.route,
                "cost": result.cost_units,
            }
            if result.failure_reason:
                attempt["failure_reason"] = result.failure_reason.value
            if result.metadata:
                attempt["metadata"] = safe_metadata(result.metadata)
            if result.success:
                screenshot_only = bool(request.screenshot and result.screenshot and not result.html)
                if not request.skip_validation and not screenshot_only:
                    validation = validate_content(result.html)
                    emit_progress(
                        id=f"validation-{provider_index}",
                        parent_id=provider_step_id,
                        name="Validate captured content",
                        kind="validation",
                        status="ok" if validation.passed else "error",
                        outcome="passed" if validation.passed else "rejected",
                        summary=validation.detail,
                        attributes={
                            "block_type": validation.block_type,
                            "matched_pattern": validation.matched_pattern,
                            "html_chars": len(result.html or ""),
                        },
                    )
                    result.content_validated = validation.passed
                    result.block_type = validation.block_type
                    result.validation_detail = validation.detail
                    if not validation.passed:
                        result.success = False
                        self.memory.remember_failure(
                            request.url, provider.name, validation.block_type
                        )
                        attempt["result"] = "validation_failed"
                        attempt["block_type"] = validation.block_type
                        attempt["validation_detail"] = validation.detail
                        attempt["matched_pattern"] = validation.matched_pattern
                        attempt["snippet"] = validation.snippet
                        attempt["chars"] = len(result.html or "")
                        force_artifacts = (
                            bool(request.metadata.get("debug_artifacts"))
                            or self.evaluator is not None
                        )
                        artifact_path = self.telemetry.write_failed_artifact(
                            run_id,
                            len(attempts) + 1,
                            provider.name,
                            result,
                            force=force_artifacts,
                        )
                        if artifact_path:
                            attempt["artifact_path"] = artifact_path
                        screenshot_artifact_path = self.telemetry.write_failed_screenshot_artifact(
                            run_id,
                            len(attempts) + 1,
                            provider.name,
                            result,
                            force=force_artifacts,
                        )
                        if screenshot_artifact_path:
                            attempt["screenshot_artifact_path"] = screenshot_artifact_path
                        attempts.append(attempt)
                        _log(
                            f"  [{provider.name}] {result.status_code} {elapsed:.1f}s → ✗ {validation.block_type or 'failed'}"
                        )
                        last_result = result
                        continue
                if result.html and not result.markdown:
                    result.markdown = md(result.html)
                attempt["result"] = "success"
                attempt["chars"] = len(result.html or "")
                elapsed_ms = int((time.perf_counter() - scrape_start) * 1000)
                result.metadata["run_id"] = run_id
                evaluation = await self._evaluate_result(
                    run_id=run_id,
                    request=request,
                    result=result,
                    attempts=[*attempts, attempt],
                    elapsed_ms=elapsed_ms,
                )
                self.cache.save(result, render_js=request.render_js)
                self.memory.remember_success(
                    request.url,
                    provider.name,
                    request.country,
                    request.render_js,
                    request.premium,
                    tier=result.route,
                )
                if result.html:
                    hreflang = _check_hreflang(result.html, request.url, request.country)
                    if hreflang:
                        result.metadata["hreflang"] = hreflang
                        if hreflang.get("country_match") is False:
                            avail = hreflang.get("available_countries", [])
                            _log(
                                f"  [hreflang] country {request.country} not in page alternatives: {', '.join(avail)}"
                            )
                        elif hreflang.get("canonical_url"):
                            _log(
                                f"  [hreflang] canonical for {request.country}: {hreflang['canonical_url']}"
                            )
                    changes = self.memory.record_scrape(request.url, result.html, provider.name)
                    if changes and changes != ["no changes"]:
                        result.metadata["changes"] = changes
                        _log(f"  [history] {'; '.join(changes)}")
                    elif changes == ["no changes"]:
                        _log("  [history] no changes since last scrape")
                attempts.append(attempt)
                _log(f"  [{provider.name}] {result.status_code} {elapsed:.1f}s → ✓ pass")
                report = self.telemetry.build_report(
                    run_id=run_id,
                    started_at=started_at,
                    finished_at=utc_now(),
                    elapsed_ms=elapsed_ms,
                    request=request,
                    use_cache=use_cache,
                    use_memory=use_memory,
                    proxy_enabled=proxy_enabled,
                    final=result,
                    attempts=attempts,
                    skipped=skipped,
                    evaluation=evaluation,
                )
                report_path = self._write_report_with_progress(report)
                if report_path:
                    result.metadata["telemetry_report"] = str(report_path)
                _log_event(
                    "scrape_done",
                    url=request.url,
                    run_id=run_id,
                    success=True,
                    provider=provider.name,
                    route=result.route,
                    cost=result.cost_units,
                    chars=len(result.html or ""),
                    elapsed_ms=elapsed_ms,
                    attempts=attempts,
                    skipped=skipped,
                    diagnosis=report["diagnosis"],
                    recommended_next_action=report["recommended_next_action"],
                )
                return result
            reason = (
                result.failure_reason.value if result.failure_reason else result.error or "failed"
            )
            if result.error and result.failure_reason in {
                FailureReason.PROXY_ERROR,
                FailureReason.PROVIDER_ERROR,
                FailureReason.UNKNOWN,
            }:
                reason = f"{reason}: {result.error}"
            attempt["result"] = "failed"
            attempt["reason"] = reason
            if result.error:
                attempt["error"] = result.error
            if result.html:
                attempt["chars"] = len(result.html)
            force_artifacts = (
                bool(request.metadata.get("debug_artifacts")) or self.evaluator is not None
            )
            artifact_path = self.telemetry.write_failed_artifact(
                run_id,
                len(attempts) + 1,
                provider.name,
                result,
                force=force_artifacts,
            )
            if artifact_path:
                attempt["artifact_path"] = artifact_path
            screenshot_artifact_path = self.telemetry.write_failed_screenshot_artifact(
                run_id,
                len(attempts) + 1,
                provider.name,
                result,
                force=force_artifacts,
            )
            if screenshot_artifact_path:
                attempt["screenshot_artifact_path"] = screenshot_artifact_path
            attempts.append(attempt)
            _log(f"  [{provider.name}] {result.status_code or 'ERR'} {elapsed:.1f}s → ✗ {reason}")
            self.memory.remember_failure(request.url, provider.name)
            last_result = result
            if result.failure_reason == FailureReason.PROXY_ERROR:
                _log("  [result] proxy configuration failed; not escalating providers")
                break

        if skipped:
            _log(f"  [skip] {', '.join(skipped)}")
        if not last_result:
            _log("  [result] no provider could handle request")

        final = last_result or ScrapeResult(
            request.url, "none", False, error="No provider could handle request"
        )
        final.metadata["run_id"] = run_id
        elapsed_ms = int((time.perf_counter() - scrape_start) * 1000)
        evaluation = await self._evaluate_result(
            run_id=run_id,
            request=request,
            result=final,
            attempts=attempts,
            elapsed_ms=elapsed_ms,
        )
        report = self.telemetry.build_report(
            run_id=run_id,
            started_at=started_at,
            finished_at=utc_now(),
            elapsed_ms=elapsed_ms,
            request=request,
            use_cache=use_cache,
            use_memory=use_memory,
            proxy_enabled=proxy_enabled,
            final=final,
            attempts=attempts,
            skipped=skipped,
            evaluation=evaluation,
        )
        report_path = self._write_report_with_progress(report)
        if report_path:
            final.metadata["telemetry_report"] = str(report_path)
        _log_event(
            "scrape_done",
            url=request.url,
            run_id=run_id,
            success=False,
            provider=final.provider,
            error=final.error,
            elapsed_ms=elapsed_ms,
            attempts=attempts,
            skipped=skipped,
            diagnosis=report["diagnosis"],
            recommended_next_action=report["recommended_next_action"],
        )
        return final

    def _ordered_providers(self, request: ScrapeRequest) -> list[ProviderAdapter]:
        providers = sorted(self.providers, key=lambda p: p.cost_rank)

        if self.strategy.provider:
            preferred = [p for p in providers if p.name == self.strategy.provider]
            rest = [p for p in providers if p.name != self.strategy.provider]
            if preferred:
                _log(f"  [strategy] preferred provider: {self.strategy.provider}")
                return preferred + rest
            _log(
                f"  [strategy] preferred provider {self.strategy.provider!r} not found, falling back"
            )

        pref = self.memory.preferred_provider(request.url)
        if pref:
            pref_name, pref_tier = pref
            pref_cost = next((p.cost_rank for p in providers if p.name == pref_name), None)
            if pref_cost is not None:
                skipped_names = [p.name for p in providers if p.cost_rank < pref_cost]
                providers = [p for p in providers if p.cost_rank >= pref_cost]
                providers = sorted(providers, key=lambda p: 0 if p.name == pref_name else 1)
                tier_info = f" ({pref_tier})" if pref_tier else ""
                skip_info = f", skip {'/'.join(skipped_names)}" if skipped_names else ""
                _log(f"  [memory] prefer {pref_name}{tier_info}{skip_info}")
            if pref_tier:
                request.metadata["start_tier"] = pref_tier
        else:
            _log("  [memory] no history")
        return providers
