from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .models import FailureReason, ScrapeRequest, ScrapeResult

MAX_ERROR_CHARS = 1_000
MAX_SNIPPET_CHARS = 600
MAX_METADATA_CHARS = 2_000
SENSITIVE_METADATA_KEY_PARTS = {
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "passwd",
    "privatekey",
    "secret",
    "token",
}


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


def evidence_snippet(
    html: str | None, pattern: str | None, *, limit: int = MAX_SNIPPET_CHARS
) -> str | None:
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


def _is_sensitive_metadata_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", key.lower())
    return any(part in normalized for part in SENSITIVE_METADATA_KEY_PARTS)


def _safe_metadata_value(value: Any) -> Any:
    if isinstance(value, str):
        return truncate(value, MAX_METADATA_CHARS)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return safe_metadata(value)
    if isinstance(value, list):
        return [_safe_metadata_value(item) for item in value[:20]]
    return truncate(str(value), MAX_METADATA_CHARS)


def safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    result: dict[str, Any] = {}
    for key, value in metadata.items():
        if _is_sensitive_metadata_key(key):
            result[key] = "<redacted>"
        else:
            result[key] = _safe_metadata_value(value)
    return result


def _redact_embedded_images(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_embedded_images(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_embedded_images(item) for item in value]
    if isinstance(value, str) and value.startswith("data:") and ";base64," in value:
        media_type = value.split(";", 1)[0].removeprefix("data:")
        suffix = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(
            media_type, "bin"
        )
        return f"<saved separately: screenshot.{suffix}>"
    return value


def _sha256(content: str | bytes | None) -> str | None:
    if content is None:
        return None
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()


def _screenshot_suffix(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "webp"
    return "bin"


def request_summary(
    request: ScrapeRequest, *, use_cache: bool, use_memory: bool, proxy_enabled: bool
) -> dict[str, Any]:
    return {
        "url": request.url,
        "domain": domain_for_url(request.url),
        "country": request.country,
        "render_js": request.render_js,
        "premium": request.premium,
        "screenshot": request.screenshot,
        "mobile": request.mobile,
        "wait_event": request.wait_event,
        "wait_selector": request.wait_selector,
        "extra_wait_ms": request.extra_wait_ms,
        "block_ads": request.block_ads,
        "output_format": request.output_format,
        "timeout_seconds": request.timeout_seconds,
        "referer": request.referer,
        "skip_validation": request.skip_validation,
        "evaluation_goal": request.metadata.get("evaluation_goal"),
        "metadata": safe_metadata(request.metadata),
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


def diagnose_scrape(
    success: bool, final: ScrapeResult, attempts: list[dict[str, Any]], skipped: list[str]
) -> Diagnosis:
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

    if final.failure_reason == FailureReason.TIMEOUT or any(
        a.get("failure_reason") == "timeout" for a in attempts
    ):
        return Diagnosis(
            "timeout",
            False,
            0.85,
            "try_render_js_or_increase_timeout",
            ["one or more providers timed out"],
        )

    http_5xx = [
        a
        for a in attempts
        if a.get("failure_reason") == "http_5xx" or (a.get("status") or 0) >= 500
    ]
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
    evaluated_only: bool = False,
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
        if evaluated_only and not isinstance(report.get("evaluation"), dict):
            continue
        report["_path"] = str(path)
        reports.append(report)
    reports.sort(key=lambda item: item.get("started_at", ""), reverse=True)
    return reports[:limit]


def _is_actionable_opportunity(text: str) -> bool:
    normalized = text.strip().lower().rstrip(".")
    if normalized in {"none", "n/a", "not applicable"}:
        return False
    if re.match(r"^none(?:\s+(?:is|are))?\s+(?:needed|required|necessary)\b", normalized):
        return False
    return re.search(
        r"\bno (?:specific )?improvements? (?:are )?(?:needed|required|necessary)\b",
        normalized,
    ) is None


def summarize_evaluations(
    reports: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate persisted evaluator evidence without changing scrape behavior."""

    status_counts: Counter[str] = Counter()
    verdict_counts: Counter[str] = Counter()
    page_type_counts: Counter[str] = Counter()
    issue_counts: Counter[str] = Counter()
    issue_severity_counts: Counter[str] = Counter()
    root_cause_counts: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    opportunity_counts: Counter[str] = Counter()
    model_counts: Counter[str] = Counter()
    provider_counts: Counter[str] = Counter()
    prompt_version_counts: Counter[str] = Counter()
    check_result_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    check_failure_counts: Counter[str] = Counter()
    review_queue: list[dict[str, Any]] = []
    usage = {
        "cost": 0.0,
        "upstream_inference_cost": 0.0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_runs": 0,
    }

    evaluated_runs = 0
    for report in reports:
        evaluation = report.get("evaluation")
        if not isinstance(evaluation, dict):
            continue
        evaluated_runs += 1

        status = evaluation.get("status") or "unknown"
        status_counts[str(status)] += 1
        prompt_version = evaluation.get("prompt_version")
        if prompt_version:
            prompt_version_counts[str(prompt_version)] += 1
        page_type = evaluation.get("page_type")
        if page_type:
            page_type_counts[str(page_type)] += 1
        for field, counter in (
            ("verdict", verdict_counts),
            ("root_cause", root_cause_counts),
            ("recommended_action", action_counts),
            ("model", model_counts),
            ("provider", provider_counts),
        ):
            value = evaluation.get(field)
            if value:
                counter[str(value)] += 1

        for issue in evaluation.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            if issue.get("code"):
                issue_counts[str(issue["code"])] += 1
            if issue.get("severity"):
                issue_severity_counts[str(issue["severity"])] += 1

        for opportunity in evaluation.get("improvement_opportunities") or []:
            if (
                isinstance(opportunity, str)
                and opportunity.strip()
                and _is_actionable_opportunity(opportunity)
            ):
                opportunity_counts[opportunity.strip()] += 1

        failed_checks: list[str] = []
        checks = evaluation.get("checks") or {}
        if isinstance(checks, dict):
            for check_name, check in checks.items():
                if not isinstance(check, dict) or not check.get("result"):
                    continue
                result = str(check["result"])
                check_name = str(check_name)
                check_result_counts[check_name][result] += 1
                if result == "fail":
                    check_failure_counts[check_name] += 1
                    failed_checks.append(check_name)

        evaluation_usage = evaluation.get("usage") or {}
        for token_field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = evaluation_usage.get(token_field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                usage[token_field] += int(value)
        cost = evaluation_usage.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            usage["cost"] += float(cost)
        upstream_cost = (evaluation_usage.get("cost_details") or {}).get("upstream_inference_cost")
        if isinstance(upstream_cost, (int, float)) and not isinstance(upstream_cost, bool):
            usage["upstream_inference_cost"] += float(upstream_cost)
        if evaluation.get("cached") or evaluation_usage.get("cache_hit"):
            usage["cached_runs"] += 1

        needs_human_review = evaluation.get("needs_human_review") is True
        needs_review = (
            status != "completed"
            or evaluation.get("verdict") != "pass"
            or needs_human_review
        )
        if needs_review:
            review_queue.append(
                {
                    "run_id": report.get("run_id"),
                    "url": report.get("url"),
                    "domain": report.get("domain"),
                    "status": status,
                    "verdict": evaluation.get("verdict"),
                    "needs_human_review": needs_human_review,
                    "failed_checks": failed_checks,
                    "prompt_version": prompt_version,
                    "root_cause": evaluation.get("root_cause"),
                    "recommended_action": evaluation.get("recommended_action"),
                    "error": evaluation.get("error"),
                    "report_path": report.get("_path"),
                }
            )

    usage["cost"] = round(usage["cost"], 10)
    usage["upstream_inference_cost"] = round(usage["upstream_inference_cost"], 10)
    opportunities = [
        {"text": text, "count": count}
        for text, count in sorted(
            opportunity_counts.items(), key=lambda item: (-item[1], item[0].lower())
        )
    ]

    return {
        "calibration_status": "uncalibrated_audit",
        "runs_scanned": len(reports),
        "evaluated_runs": evaluated_runs,
        "unevaluated_runs": len(reports) - evaluated_runs,
        "status_counts": dict(sorted(status_counts.items())),
        "verdict_counts": dict(sorted(verdict_counts.items())),
        "page_type_counts": dict(sorted(page_type_counts.items())),
        "root_cause_counts": dict(sorted(root_cause_counts.items())),
        "recommended_action_counts": dict(sorted(action_counts.items())),
        "issue_counts": dict(sorted(issue_counts.items())),
        "issue_severity_counts": dict(sorted(issue_severity_counts.items())),
        "model_counts": dict(sorted(model_counts.items())),
        "provider_counts": dict(sorted(provider_counts.items())),
        "prompt_version_counts": dict(sorted(prompt_version_counts.items())),
        "check_result_counts": {
            check: dict(sorted(counts.items()))
            for check, counts in sorted(check_result_counts.items())
        },
        "check_failure_counts": dict(sorted(check_failure_counts.items())),
        "improvement_opportunities": opportunities,
        "usage": usage,
        "review_queue": review_queue,
    }


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

    def write_failed_screenshot_artifact(
        self,
        run_id: str,
        index: int,
        provider: str,
        result: ScrapeResult,
        *,
        force: bool = False,
    ) -> str | None:
        if not self.enabled or not (self.debug_artifacts or force) or not result.screenshot:
            return None
        folder = self.root / run_id
        folder.mkdir(parents=True, exist_ok=True)
        safe_provider = re.sub(r"[^a-zA-Z0-9_.-]+", "_", provider)
        suffix = _screenshot_suffix(result.screenshot)
        path = folder / f"{index:02d}-{safe_provider}.failed.{suffix}"
        path.write_bytes(result.screenshot)
        return str(path)

    def write_evaluation_artifacts(
        self,
        run_id: str,
        outcome: Any,
        result: ScrapeResult,
    ) -> dict[str, str]:
        if not self.enabled:
            return {}

        folder = self.root / run_id / "evaluation"
        folder.mkdir(parents=True, exist_ok=True)
        paths: dict[str, str] = {}

        input_path = folder / "input.md"
        input_path.write_text(outcome.markdown_evidence, encoding="utf-8")
        paths["input_markdown"] = str(input_path)

        request_path = folder / "request.json"
        request_path.write_text(
            json.dumps(
                _redact_embedded_images(outcome.request_payload),
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        paths["request"] = str(request_path)

        response_path = folder / "response.json"
        response_path.write_text(
            json.dumps(outcome.judgment or {}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        paths["response"] = str(response_path)

        if result.html is not None:
            html_path = folder / "final.html"
            html_path.write_text(result.html, encoding="utf-8")
            paths["final_html"] = str(html_path)
        if result.markdown is not None:
            markdown_path = folder / "final.md"
            markdown_path.write_text(result.markdown, encoding="utf-8")
            paths["final_markdown"] = str(markdown_path)
        if result.screenshot:
            suffix = _screenshot_suffix(result.screenshot)
            screenshot_path = folder / f"screenshot.{suffix}"
            screenshot_path.write_bytes(result.screenshot)
            paths["screenshot"] = str(screenshot_path)

        metadata = {
            "status": outcome.status,
            "model": outcome.model,
            "prompt_version": outcome.prompt_version,
            "calibration_status": "uncalibrated_audit",
            "generation_id": outcome.generation_id,
            "provider": outcome.provider,
            "usage": outcome.usage,
            "elapsed_ms": outcome.elapsed_ms,
            "input_modalities": outcome.input_modalities,
            "cached": outcome.cached,
            "error": outcome.error,
            "response_metadata": outcome.response_metadata,
            "content_hashes": {
                "html": _sha256(result.html),
                "markdown": _sha256(result.markdown),
                "evaluated_markdown": _sha256(outcome.markdown_evidence),
                "screenshot": _sha256(result.screenshot),
            },
            "artifacts": paths,
        }
        metadata_path = folder / "metadata.json"
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        paths["metadata"] = str(metadata_path)
        return paths

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
        evaluation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        diagnosis = diagnose_scrape(final.success, final, attempts, skipped)
        report = {
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
        if evaluation is not None:
            report["evaluation"] = evaluation
        return report
