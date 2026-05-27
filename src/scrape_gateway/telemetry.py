from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .models import FailureReason, ScrapeRequest, ScrapeResult

MAX_ERROR_CHARS = 1_000
MAX_SNIPPET_CHARS = 600
MAX_METADATA_CHARS = 2_000


@dataclass(slots=True)
class Diagnosis:
    code: str
    useful: bool
    confidence: float
    recommended_next_action: str
    evidence: list[str]


def new_run_id() -> str:
    return uuid.uuid4().hex[:16]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def domain_for_url(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def truncate(value: str | None, limit: int = MAX_ERROR_CHARS) -> str | None:
    if value is None:
        return None
    if len(value) <= limit:
        return value
    return f"{value[:limit]}... [truncated {len(value) - limit} chars]"


def evidence_snippet(html: str | None, pattern: str | None, *, limit: int = MAX_SNIPPET_CHARS) -> str | None:
    if not html or not pattern:
        return None
    lower = html.lower()
    idx = lower.find(pattern.lower())
    if idx < 0:
        return None
    radius = max(80, (limit - len(pattern)) // 2)
    start = max(0, idx - radius)
    end = min(len(html), idx + len(pattern) + radius)
    snippet = re.sub(r"\s+", " ", html[start:end]).strip()
    return truncate(snippet, limit)


def safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    result: dict[str, Any] = {}
    for key, value in metadata.items():
        if key.lower() in {"api_key", "token", "authorization", "password"}:
            result[key] = "<redacted>"
        elif isinstance(value, str):
            result[key] = truncate(value, MAX_METADATA_CHARS)
        elif isinstance(value, (int, float, bool)) or value is None:
            result[key] = value
        elif isinstance(value, dict):
            result[key] = safe_metadata(value)
        elif isinstance(value, list):
            result[key] = [
                truncate(item, MAX_METADATA_CHARS) if isinstance(item, str) else item
                for item in value[:20]
            ]
        else:
            result[key] = truncate(str(value), MAX_METADATA_CHARS)
    return result


def request_summary(request: ScrapeRequest, *, use_cache: bool, use_memory: bool, proxy_enabled: bool) -> dict[str, Any]:
    return {
        "url": request.url,
        "domain": domain_for_url(request.url),
        "country": request.country,
        "render_js": request.render_js,
        "premium": request.premium,
        "mobile": request.mobile,
        "wait_event": request.wait_event,
        "wait_selector": request.wait_selector,
        "output_format": request.output_format,
        "timeout_seconds": request.timeout_seconds,
        "cache_read_enabled": use_cache,
        "memory_enabled": use_memory,
        "proxy_enabled": proxy_enabled,
    }


def result_summary(result: ScrapeResult) -> dict[str, Any]:
    return {
        "provider": result.provider,
        "success": result.success,
        "status": result.status_code,
        "route": result.route,
        "cost": result.cost_units,
        "failure_reason": result.failure_reason.value if result.failure_reason else None,
        "error": truncate(result.error),
        "chars": len(result.html or ""),
        "markdown_chars": len(result.markdown or ""),
        "content_validated": result.content_validated,
        "block_type": result.block_type,
        "validation_detail": result.validation_detail,
        "metadata": safe_metadata(result.metadata),
    }


def diagnose_scrape(success: bool, final: ScrapeResult, attempts: list[dict[str, Any]], skipped: list[str]) -> Diagnosis:
    evidence: list[str] = []

    if success and final.provider == "cache":
        return Diagnosis("cache_hit", True, 1.0, "none", ["served from cache"])

    if success:
        for attempt in attempts:
            metadata = attempt.get("metadata") or {}
            if metadata.get("proxy_fallback"):
                return Diagnosis(
                    "success_after_proxy_fallback",
                    True,
                    0.95,
                    "fix_or_remove_scrape_proxy_url",
                    ["configured proxy failed, direct retry succeeded"],
                )
        return Diagnosis("success", True, 1.0, "none", ["content validated and cached"])

    if final.failure_reason == FailureReason.PROXY_ERROR or any(
        str(a.get("reason", "")).startswith("proxy_error") for a in attempts
    ):
        return Diagnosis(
            "proxy_auth_failed",
            False,
            0.98,
            "fix_or_remove_scrape_proxy_url",
            ["provider returned proxy authentication/connect error"],
        )

    validation_failures = [a for a in attempts if a.get("result") == "validation_failed"]
    if validation_failures:
        last = validation_failures[-1]
        if last.get("matched_pattern"):
            evidence.append(f"matched validator pattern: {last['matched_pattern']}")
        if last.get("snippet"):
            evidence.append(f"snippet: {last['snippet']}")
        evidence.append(f"block_type: {last.get('block_type')}")
        return Diagnosis(
            "validator_rejected",
            False,
            0.8,
            "inspect_validator_evidence_or_try_render_js",
            evidence,
        )

    if final.failure_reason == FailureReason.TIMEOUT or any(a.get("failure_reason") == "timeout" for a in attempts):
        return Diagnosis(
            "timeout",
            False,
            0.85,
            "try_render_js_or_increase_timeout",
            ["one or more providers timed out"],
        )

    http_5xx = [a for a in attempts if a.get("failure_reason") == "http_5xx" or (a.get("status") or 0) >= 500]
    if http_5xx:
        providers = ", ".join(a.get("provider", "?") for a in http_5xx)
        return Diagnosis(
            "provider_5xx",
            False,
            0.8,
            "check_paid_provider_health_or_retry_later",
            [f"5xx from: {providers}"],
        )

    if not attempts and skipped:
        return Diagnosis(
            "no_provider_available",
            False,
            0.95,
            "enable_provider_or_adjust_request_capabilities",
            [f"skipped: {', '.join(skipped)}"],
        )

    return Diagnosis(
        "all_providers_failed",
        False,
        0.6,
        "inspect_attempts_and_provider_errors",
        ["no deterministic diagnosis matched"],
    )


def load_recent_reports(
    root: str | Path = ".scrape-gateway/runs",
    *,
    limit: int = 20,
    domain: str | None = None,
    diagnosis: str | None = None,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    root_path = Path(root)
    if not root_path.exists():
        return []
    for path in root_path.glob("*/report.json"):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if domain and report.get("domain") != domain:
            continue
        if diagnosis and report.get("diagnosis") != diagnosis:
            continue
        report["_path"] = str(path)
        reports.append(report)
    reports.sort(key=lambda item: item.get("started_at", ""), reverse=True)
    return reports[:limit]


class TelemetryRecorder:
    def __init__(
        self,
        root: str | Path = ".scrape-gateway/runs",
        *,
        enabled: bool = True,
        debug_artifacts: bool = False,
    ) -> None:
        self.root = Path(root)
        self.enabled = enabled
        self.debug_artifacts = debug_artifacts
        if enabled:
            self.root.mkdir(parents=True, exist_ok=True)

    def write_report(self, report: dict[str, Any]) -> Path | None:
        if not self.enabled:
            return None
        run_id = report["run_id"]
        folder = self.root / run_id
        folder.mkdir(parents=True, exist_ok=True)
        report_path = folder / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        attempts_path = folder / "attempts.jsonl"
        attempts_path.write_text(
            "".join(json.dumps(a, ensure_ascii=False) + "\n" for a in report.get("attempts", [])),
            encoding="utf-8",
        )
        return report_path

    def write_failed_artifact(
        self,
        run_id: str,
        index: int,
        provider: str,
        result: ScrapeResult,
        *,
        force: bool = False,
    ) -> str | None:
        if not self.enabled or not (self.debug_artifacts or force) or not result.html:
            return None
        folder = self.root / run_id
        folder.mkdir(parents=True, exist_ok=True)
        safe_provider = re.sub(r"[^a-zA-Z0-9_.-]+", "_", provider)
        path = folder / f"{index:02d}-{safe_provider}.failed.html"
        path.write_text(result.html, encoding="utf-8")
        return str(path)

    def build_report(
        self,
        *,
        run_id: str,
        started_at: str,
        finished_at: str,
        elapsed_ms: int,
        request: ScrapeRequest,
        use_cache: bool,
        use_memory: bool,
        proxy_enabled: bool,
        final: ScrapeResult,
        attempts: list[dict[str, Any]],
        skipped: list[str],
    ) -> dict[str, Any]:
        diagnosis = diagnose_scrape(final.success, final, attempts, skipped)
        return {
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "elapsed_ms": elapsed_ms,
            "url": request.url,
            "domain": domain_for_url(request.url),
            "request": request_summary(
                request,
                use_cache=use_cache,
                use_memory=use_memory,
                proxy_enabled=proxy_enabled,
            ),
            "success": final.success,
            "useful": diagnosis.useful,
            "diagnosis": diagnosis.code,
            "confidence": diagnosis.confidence,
            "recommended_next_action": diagnosis.recommended_next_action,
            "evidence": diagnosis.evidence,
            "final": result_summary(final),
            "attempts": attempts,
            "skipped": skipped,
        }
