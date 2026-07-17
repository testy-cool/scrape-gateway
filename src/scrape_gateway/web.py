"""Operator console HTTP API and packaged frontend."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .config import GatewayConfig, load_config
from .models import ScrapeRequest, ScrapeResult
from .telemetry import load_recent_reports, result_summary, summarize_evaluations
from .progress import observe_progress

PREVIEW_LIMIT = 250_000
RUN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
ARTIFACT_SUFFIXES = {".html", ".json", ".jsonl", ".md", ".png", ".jpg", ".jpeg", ".webp", ".txt"}
ASSET_ROOT = Path(__file__).with_name("web_assets")


def _package_version() -> str:
    try:
        return version("scrape-gateway")
    except PackageNotFoundError:
        return "dev"


def _asset_version(asset_root: Path) -> str:
    digest = hashlib.sha256()
    digest.update((asset_root / "app.css").read_bytes())
    digest.update(b"\0")
    digest.update((asset_root / "app.js").read_bytes())
    return digest.hexdigest()[:12]


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        {"error": "A valid Scrape Gateway bearer token is required."},
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _is_authorized(request: Request, token: str) -> bool:
    if not token:
        return True
    scheme, _, credential = request.headers.get("authorization", "").partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(credential, token)


def _json_error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def _telemetry_root(get_config: Callable[[], GatewayConfig]) -> Path:
    return Path(get_config().telemetry.root).expanduser()


def _settings_payload(config: GatewayConfig) -> dict[str, Any]:
    from .discovery import discover_providers

    available = discover_providers()
    configured = {provider.name: provider for provider in config.providers}
    names = set(available) | set(configured)
    providers = []
    for name in sorted(
        names, key=lambda item: (getattr(available.get(item), "cost_rank", 999), item)
    ):
        provider = configured.get(name)
        provider_type = available.get(name)
        timeout = provider.timeout_seconds if provider else None
        providers.append(
            {
                "name": name,
                "enabled": provider.enabled if provider else True,
                "available": provider_type is not None,
                "timeout_seconds": timeout,
                "effective_timeout_seconds": timeout or config.request.default_timeout_seconds,
                "cost_rank": getattr(provider_type, "cost_rank", None),
                "capabilities": sorted(getattr(provider_type, "capabilities", [])),
            }
        )
    return {
        "default_timeout_seconds": config.request.default_timeout_seconds,
        "evaluation_timeout_seconds": config.evaluation.timeout_seconds,
        "providers": providers,
        "providers_by_name": {provider["name"]: provider for provider in providers},
    }


def _positive_timeout(value: Any, field: str, *, optional: bool = False) -> float | None:
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number.")
    timeout = float(value)
    if not 1 <= timeout <= 600:
        raise ValueError(f"{field} must be between 1 and 600 seconds.")
    return timeout


def _validated_settings(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Settings must be a JSON object.")
    providers_value = payload.get("providers")
    if not isinstance(providers_value, list) or not providers_value:
        raise ValueError("providers must be a non-empty list.")
    providers = []
    seen = set()
    for value in providers_value:
        if not isinstance(value, dict) or not isinstance(value.get("name"), str):
            raise ValueError("Each provider must include a name.")
        name = value["name"].strip()
        if not name or name in seen:
            raise ValueError("Provider names must be unique and non-empty.")
        if not isinstance(value.get("enabled"), bool):
            raise ValueError(f"enabled must be true or false for {name}.")
        seen.add(name)
        providers.append(
            {
                "name": name,
                "enabled": value["enabled"],
                "timeout_seconds": _positive_timeout(
                    value.get("timeout_seconds"), f"timeout_seconds for {name}", optional=True
                ),
            }
        )
    if not any(provider["enabled"] for provider in providers):
        raise ValueError("At least one provider must remain enabled.")
    return {
        "default_timeout_seconds": _positive_timeout(
            payload.get("default_timeout_seconds"), "default_timeout_seconds"
        ),
        "evaluation_timeout_seconds": _positive_timeout(
            payload.get("evaluation_timeout_seconds"), "evaluation_timeout_seconds"
        ),
        "providers": providers,
    }


def _run_dir(root: Path, run_id: str) -> Path | None:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        return None
    candidate = root / run_id
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_dir() else None


def _artifact_path(root: Path, run_id: str, relative_path: str) -> Path | None:
    run_dir = _run_dir(root, run_id)
    if run_dir is None or not relative_path or "\x00" in relative_path:
        return None
    try:
        candidate = (run_dir / relative_path).resolve()
        candidate.relative_to(run_dir)
    except (OSError, ValueError):
        return None
    if not candidate.is_file() or candidate.suffix.lower() not in ARTIFACT_SUFFIXES:
        return None
    return candidate


def _artifact_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return "image"
    if suffix in {".json", ".jsonl"}:
        return "json"
    if suffix == ".md":
        return "markdown"
    if suffix == ".html":
        return "html"
    return "text"


def _list_artifacts(root: Path, run_id: str) -> list[dict[str, Any]]:
    run_dir = _run_dir(root, run_id)
    if run_dir is None:
        return []
    artifacts = []
    for path in sorted(run_dir.rglob("*")):
        try:
            resolved = path.resolve()
            resolved.relative_to(run_dir)
        except (OSError, ValueError):
            continue
        if not resolved.is_file() or resolved.suffix.lower() not in ARTIFACT_SUFFIXES:
            continue
        relative = resolved.relative_to(run_dir).as_posix()
        artifacts.append(
            {
                "name": resolved.name,
                "path": relative,
                "kind": _artifact_kind(resolved),
                "size": resolved.stat().st_size,
                "url": f"/api/runs/{run_id}/artifacts/{quote(relative, safe='/')}",
            }
        )
    return artifacts


def _run_summary(report: dict[str, Any]) -> dict[str, Any]:
    final = report.get("final") if isinstance(report.get("final"), dict) else {}
    evaluation = report.get("evaluation")
    evaluation_summary = None
    if isinstance(evaluation, dict):
        evaluation_summary = {
            key: evaluation.get(key)
            for key in (
                "status",
                "verdict",
                "needs_human_review",
                "page_type",
                "root_cause",
                "recommended_action",
                "model",
                "cached",
            )
        }
    return {
        "run_id": report.get("run_id"),
        "started_at": report.get("started_at"),
        "elapsed_ms": report.get("elapsed_ms"),
        "url": report.get("url"),
        "domain": report.get("domain"),
        "success": report.get("success"),
        "useful": report.get("useful"),
        "diagnosis": report.get("diagnosis"),
        "provider": final.get("provider"),
        "route": final.get("route"),
        "status_code": final.get("status"),
        "chars": final.get("chars"),
        "markdown_chars": final.get("markdown_chars"),
        "evaluation": evaluation_summary,
    }


def _preview_text(value: str | None) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    return value[:PREVIEW_LIMIT], len(value) > PREVIEW_LIMIT


def _result_payload(result: ScrapeResult) -> dict[str, Any]:
    markdown, markdown_truncated = _preview_text(result.markdown)
    html, html_truncated = _preview_text(result.html)
    return {
        "run_id": result.metadata.get("run_id"),
        "success": result.success,
        "url": result.url,
        "provider": result.provider,
        "route": result.route,
        "status_code": result.status_code,
        "failure_reason": result.failure_reason.value if result.failure_reason else None,
        "error": result.error,
        "result": result_summary(result),
        "evaluation": result.metadata.get("evaluation"),
        "telemetry_report": result.metadata.get("telemetry_report"),
        "preview": {
            "markdown": markdown,
            "markdown_truncated": markdown_truncated,
            "html": html,
            "html_truncated": html_truncated,
            "has_screenshot": result.screenshot is not None,
        },
    }


def _recorded_milliseconds(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(int(value), 0)


def _trace_step(
    step_id: str,
    name: str,
    kind: str,
    status: str,
    outcome: str,
    summary: str,
    *,
    offset_ms: int,
    duration_ms: int | None = None,
    parent_id: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": step_id,
        "parent_id": parent_id,
        "name": name,
        "kind": kind,
        "status": status,
        "outcome": outcome,
        "summary": summary,
        "offset_ms": offset_ms,
        "duration_ms": duration_ms,
        "timing": "recorded" if duration_ms is not None else "order_only",
        "attributes": attributes or {},
    }


def _trace_payload(report: dict[str, Any], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    request = report.get("request") if isinstance(report.get("request"), dict) else {}
    final = report.get("final") if isinstance(report.get("final"), dict) else {}
    attempts = report.get("attempts") if isinstance(report.get("attempts"), list) else []
    skipped = report.get("skipped") if isinstance(report.get("skipped"), list) else []
    evaluation = report.get("evaluation")
    total_ms = _recorded_milliseconds(report.get("elapsed_ms")) or 0
    steps: list[dict[str, Any]] = []
    cursor_ms = 0

    steps.append(
        _trace_step(
            "request",
            "Request accepted",
            "request",
            "ok",
            "accepted",
            str(report.get("url") or request.get("url") or "Scrape request"),
            offset_ms=0,
            attributes=request,
        )
    )

    if request.get("cache_read_enabled") is True:
        cache_hit = final.get("provider") == "cache"
        steps.append(
            _trace_step(
                "cache",
                "Cache lookup",
                "cache",
                "ok" if cache_hit else "info",
                "hit" if cache_hit else "miss",
                (
                    "A reusable response satisfied the request."
                    if cache_hit
                    else "No reusable response satisfied the request."
                ),
                offset_ms=0,
                attributes={
                    "enabled": True,
                    "source_provider": final.get("metadata", {}).get("cache_source_provider")
                    if isinstance(final.get("metadata"), dict)
                    else None,
                },
            )
        )

    for index, attempt_value in enumerate(attempts, start=1):
        if not isinstance(attempt_value, dict):
            continue
        attempt = attempt_value
        provider_id = f"provider-{index}"
        result = str(attempt.get("result") or attempt.get("failure_reason") or "failed")
        succeeded = result == "success"
        duration_ms = _recorded_milliseconds(attempt.get("elapsed_ms"))
        route = attempt.get("route") or "No route recorded"
        http_status = f"HTTP {attempt['status']}" if attempt.get("status") else "No HTTP status"
        failure = attempt.get("reason") or attempt.get("error") or attempt.get("block_type")
        summary = " · ".join(str(value) for value in (route, http_status, failure) if value)
        steps.append(
            _trace_step(
                provider_id,
                str(attempt.get("provider") or "Unknown provider"),
                "provider",
                "ok" if succeeded else "error",
                result,
                summary,
                offset_ms=cursor_ms,
                duration_ms=duration_ms,
                attributes=attempt,
            )
        )
        cursor_ms += duration_ms or 0

        if result in {"success", "validation_failed"}:
            rejected = result == "validation_failed"
            steps.append(
                _trace_step(
                    f"{provider_id}-validation",
                    "Content validation",
                    "validation",
                    "error" if rejected else "ok",
                    "rejected" if rejected else "passed",
                    str(
                        attempt.get("validation_detail")
                        or attempt.get("block_type")
                        or "Content passed deterministic checks."
                    ),
                    offset_ms=cursor_ms,
                    parent_id=provider_id,
                    attributes={
                        key: attempt.get(key)
                        for key in (
                            "block_type",
                            "validation_detail",
                            "matched_pattern",
                            "snippet",
                            "chars",
                        )
                        if attempt.get(key) is not None
                    },
                )
            )

    for index, skipped_value in enumerate(skipped, start=1):
        label = str(skipped_value)
        match = re.fullmatch(r"(.+?)\((.+)\)", label)
        provider = match.group(1) if match else label
        reason = match.group(2) if match else "not selected"
        steps.append(
            _trace_step(
                f"skipped-{index}",
                provider,
                "provider",
                "skipped",
                reason.replace(" ", "_"),
                f"Provider skipped: {reason}.",
                offset_ms=cursor_ms,
                attributes={"reason": reason},
            )
        )

    markdown_chars = _recorded_milliseconds(final.get("markdown_chars"))
    if markdown_chars:
        html_chars = _recorded_milliseconds(final.get("chars")) or 0
        steps.append(
            _trace_step(
                "transform",
                "Content normalized",
                "transform",
                "ok",
                str(request.get("output_format") or "markdown"),
                f"Produced {markdown_chars:,} Markdown characters from {html_chars:,} HTML characters.",
                offset_ms=cursor_ms,
                attributes={
                    "html_chars": html_chars,
                    "markdown_chars": markdown_chars,
                    "output_format": request.get("output_format"),
                },
            )
        )

    audit_verdict = None
    if isinstance(evaluation, dict):
        audit_verdict = evaluation.get("verdict")
        evaluation_duration = _recorded_milliseconds(evaluation.get("elapsed_ms"))
        evaluation_offset = max(
            cursor_ms,
            total_ms - evaluation_duration if evaluation_duration is not None else cursor_ms,
        )
        evaluation_status = str(evaluation.get("status") or "unknown")
        if evaluation_status in {"error", "failed"}:
            status = "error"
        elif audit_verdict == "fail" or evaluation.get("needs_human_review") is True:
            status = "warning"
        elif audit_verdict == "pass":
            status = "ok"
        else:
            status = "info"
        root_cause = evaluation.get("root_cause")
        action = evaluation.get("recommended_action")
        summary = " · ".join(
            str(value)
            for value in (
                f"Verdict: {audit_verdict}" if audit_verdict else evaluation_status,
                f"Root cause: {root_cause}" if root_cause else None,
                f"Action: {action}" if action else None,
            )
            if value
        )
        steps.append(
            _trace_step(
                "evaluation",
                "AI quality evaluation",
                "evaluation",
                status,
                str(audit_verdict or evaluation_status),
                summary,
                offset_ms=evaluation_offset,
                duration_ms=evaluation_duration,
                attributes=evaluation,
            )
        )

    final_status = "ok" if report.get("success") is True else "error"
    diagnosis = str(report.get("diagnosis") or ("success" if final_status == "ok" else "failed"))
    steps.append(
        _trace_step(
            "result",
            "Result finalized",
            "result",
            final_status,
            diagnosis,
            " · ".join(
                str(value)
                for value in (
                    final.get("provider"),
                    final.get("route"),
                    f"HTTP {final['status']}" if final.get("status") else None,
                )
                if value
            )
            or diagnosis,
            offset_ms=total_ms,
            attributes={
                "diagnosis": diagnosis,
                "recommended_next_action": report.get("recommended_next_action"),
                **final,
            },
        )
    )

    artifact_bytes = sum(
        artifact.get("size", 0) for artifact in artifacts if isinstance(artifact.get("size"), int)
    )
    steps.append(
        _trace_step(
            "persistence",
            "Evidence persisted",
            "persistence",
            "ok" if artifacts else "info",
            "saved" if artifacts else "none",
            f"Saved {len(artifacts)} artifact{'s' if len(artifacts) != 1 else ''} for inspection.",
            offset_ms=total_ms,
            attributes={
                "artifact_count": len(artifacts),
                "total_bytes": artifact_bytes,
                "paths": [artifact.get("path") for artifact in artifacts],
            },
        )
    )

    return {
        "run_id": report.get("run_id"),
        "started_at": report.get("started_at"),
        "finished_at": report.get("finished_at"),
        "duration_ms": total_ms,
        "status": final_status,
        "audit_verdict": audit_verdict,
        "steps": steps,
    }


def create_console_routes(
    *,
    token: str = "",
    get_gateway: Callable[[], Any],
    get_config: Callable[[], GatewayConfig] = load_config,
    asset_root: Path = ASSET_ROOT,
    apply_settings: Callable[[dict[str, Any]], GatewayConfig] | None = None,
) -> list:
    active_scrapes: dict[str, dict[str, Any]] = {}
    active_tasks: set[asyncio.Task[Any]] = set()

    def release_active_task(task: asyncio.Task[Any]) -> None:
        active_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def homepage(request: Request) -> Response:
        markup = (asset_root / "index.html").read_text(encoding="utf-8")
        markup = markup.replace("__ASSET_VERSION__", _asset_version(asset_root))
        return HTMLResponse(markup, headers={"Cache-Control": "no-cache"})

    async def status(request: Request) -> Response:
        return JSONResponse(
            {
                "service": "scrape-gateway",
                "version": _package_version(),
                "token_required": bool(token),
            }
        )

    async def session(request: Request) -> Response:
        if not _is_authorized(request, token):
            return _unauthorized()
        config = get_config()
        gateway = get_gateway()
        return JSONResponse(
            {
                "authenticated": True,
                "version": _package_version(),
                "evaluation": {
                    "mode": config.evaluation.mode,
                    "model": config.evaluation.model,
                    "include_screenshot": config.evaluation.include_screenshot,
                },
                "providers": [provider.name for provider in gateway.providers],
            }
        )

    async def scrape_page(request: Request) -> Response:
        if not _is_authorized(request, token):
            return _unauthorized()
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return _json_error("Request body must be valid JSON.")
        if not isinstance(payload, dict):
            return _json_error("Request body must be a JSON object.")

        url = payload.get("url")
        if not isinstance(url, str) or not url.strip():
            return _json_error("url is required.")
        if len(url) > 4096:
            return _json_error("url is too long.")
        output_format = payload.get("output_format", "markdown")
        if output_format not in {"html", "markdown"}:
            return _json_error("output_format must be html or markdown.")
        evaluation_goal = payload.get("evaluation_goal")
        if evaluation_goal is not None and not isinstance(evaluation_goal, str):
            return _json_error("evaluation_goal must be text.")
        if isinstance(evaluation_goal, str) and len(evaluation_goal) > 4000:
            return _json_error("evaluation_goal is too long.")

        metadata = {}
        if evaluation_goal and evaluation_goal.strip():
            metadata["evaluation_goal"] = evaluation_goal.strip()
        scrape_request = ScrapeRequest(
            url.strip(),
            country=payload.get("country") or None,
            render_js=payload.get("render_js") is True,
            premium=payload.get("premium") is True,
            screenshot=payload.get("screenshot") is True,
            mobile=payload.get("mobile") is True,
            block_ads=payload.get("block_ads") is True,
            output_format=output_format,
            metadata=metadata,
        )
        use_cache = payload.get("use_cache", True) is not False

        active_id = secrets.token_hex(8)
        active_entry = {
            "pending": True,
            "run_id": active_id,
            "url": scrape_request.url,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "running",
            "diagnosis": "in_progress",
            "payload": {
                "url": scrape_request.url,
                "evaluation_goal": metadata.get("evaluation_goal", ""),
                "output_format": output_format,
                "screenshot": scrape_request.screenshot,
                "render_js": scrape_request.render_js,
                "mobile": scrape_request.mobile,
                "premium": scrape_request.premium,
                "block_ads": scrape_request.block_ads,
                "use_cache": use_cache,
            },
        }
        active_entry["steps"] = [
            {
                "id": "request",
                "parent_id": None,
                "name": "Request submitted",
                "kind": "request",
                "status": "ok",
                "outcome": "accepted",
                "summary": scrape_request.url,
                "offset_ms": 0,
                "duration_ms": None,
                "timing": "order_only",
                "attributes": active_entry["payload"],
            }
        ]
        active_scrapes[active_id] = active_entry
        scrape_request.metadata["run_id"] = active_id
        progress_start = time.perf_counter()

        def record_progress(event: dict[str, Any]) -> None:
            step = {
                "parent_id": None,
                "status": "running",
                "outcome": "in_progress",
                "summary": "",
                "duration_ms": None,
                "timing": "recorded" if event.get("duration_ms") is not None else "order_only",
                "attributes": {},
                **event,
            }
            existing = next(
                (item for item in active_entry["steps"] if item["id"] == step["id"]), None
            )
            if existing is None:
                step["offset_ms"] = int((time.perf_counter() - progress_start) * 1000)
                active_entry["steps"].append(step)
            else:
                offset_ms = existing.get("offset_ms", 0)
                existing.update(step)
                existing["offset_ms"] = offset_ms
            active_entry["activity"] = step["summary"]
            active_entry["updated_at"] = datetime.now(timezone.utc).isoformat()

        async def execute_scrape() -> ScrapeResult:
            try:
                with observe_progress(record_progress):
                    return await get_gateway().scrape(
                        scrape_request,
                        use_cache=use_cache,
                        use_memory=use_cache,
                    )
            finally:
                active_scrapes.pop(active_id, None)

        scrape_task = asyncio.create_task(execute_scrape())
        active_tasks.add(scrape_task)
        scrape_task.add_done_callback(release_active_task)
        try:
            result = await asyncio.shield(scrape_task)
        except Exception as exc:  # noqa: BLE001 - return an operator-visible API error
            return _json_error(f"Scrape failed before producing a result: {exc}", 500)
        return JSONResponse(_result_payload(result))

    async def settings(request: Request) -> Response:
        if not _is_authorized(request, token):
            return _unauthorized()
        if request.method == "GET":
            return JSONResponse(_settings_payload(get_config()))
        if apply_settings is None:
            return _json_error("Runtime settings are read-only for this service.", 405)
        try:
            payload = await request.json()
            validated = _validated_settings(payload)
            config = apply_settings(validated)
        except json.JSONDecodeError:
            return _json_error("Request body must be valid JSON.")
        except ValueError as exc:
            return _json_error(str(exc))
        return JSONResponse(_settings_payload(config))

    async def list_runs(request: Request) -> Response:
        if not _is_authorized(request, token):
            return _unauthorized()
        try:
            limit = min(max(int(request.query_params.get("limit", "100")), 1), 500)
        except ValueError:
            return _json_error("limit must be an integer.")
        reports = load_recent_reports(
            _telemetry_root(get_config),
            limit=limit,
            domain=request.query_params.get("domain") or None,
            evaluated_only=request.query_params.get("evaluated") == "true",
        )
        active = sorted(
            active_scrapes.values(),
            key=lambda item: item["started_at"],
            reverse=True,
        )
        if request.query_params.get("evaluated") == "true":
            active = []
        domain = request.query_params.get("domain")
        if domain:
            active = [item for item in active if domain in item["url"]]
        return JSONResponse(
            {
                "runs": [_run_summary(report) for report in reports],
                "active_runs": active,
            }
        )

    async def run_detail(request: Request) -> Response:
        if not _is_authorized(request, token):
            return _unauthorized()
        run_id = request.path_params["run_id"]
        root = _telemetry_root(get_config)
        run_dir = _run_dir(root, run_id)
        if run_dir is None:
            return _json_error("Run not found.", 404)
        report_path = run_dir / "report.json"
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _json_error("Run report is unavailable.", 404)
        artifacts = _list_artifacts(root, run_id)
        return JSONResponse(
            {
                "report": report,
                "trace": _trace_payload(report, artifacts),
                "artifacts": artifacts,
            }
        )

    async def artifact(request: Request) -> Response:
        if not _is_authorized(request, token):
            return _unauthorized()
        path = _artifact_path(
            _telemetry_root(get_config),
            request.path_params["run_id"],
            request.path_params["artifact_path"],
        )
        if path is None:
            return _json_error("Artifact not found.", 404)
        media_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".json": "application/json",
            ".jsonl": "application/x-ndjson",
        }
        response = FileResponse(path, media_type=media_types.get(path.suffix.lower(), "text/plain"))
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    async def evaluation_summary(request: Request) -> Response:
        if not _is_authorized(request, token):
            return _unauthorized()
        try:
            limit = min(max(int(request.query_params.get("limit", "500")), 1), 2000)
        except ValueError:
            return _json_error("limit must be an integer.")
        reports = load_recent_reports(
            _telemetry_root(get_config),
            limit=limit,
            domain=request.query_params.get("domain") or None,
        )
        return JSONResponse({"summary": summarize_evaluations(reports)})

    return [
        Route("/", homepage, methods=["GET"]),
        Route("/api/status", status, methods=["GET"]),
        Route("/api/session", session, methods=["GET"]),
        Route("/api/scrapes", scrape_page, methods=["POST"]),
        Route("/api/settings", settings, methods=["GET", "PUT"]),
        Route("/api/runs", list_runs, methods=["GET"]),
        Route("/api/runs/{run_id}", run_detail, methods=["GET"]),
        Route(
            "/api/runs/{run_id}/artifacts/{artifact_path:path}",
            artifact,
            methods=["GET"],
        ),
        Route("/api/evaluations", evaluation_summary, methods=["GET"]),
        Mount("/assets", app=StaticFiles(directory=asset_root), name="assets"),
    ]


def create_console_app(
    *,
    token: str = "",
    get_gateway: Callable[[], Any],
    get_config: Callable[[], GatewayConfig] = load_config,
    asset_root: Path = ASSET_ROOT,
    apply_settings: Callable[[dict[str, Any]], GatewayConfig] | None = None,
) -> Starlette:
    return Starlette(
        routes=create_console_routes(
            token=token,
            get_gateway=get_gateway,
            get_config=get_config,
            asset_root=asset_root,
            apply_settings=apply_settings,
        )
    )
