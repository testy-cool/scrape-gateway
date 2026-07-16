"""Operator console HTTP API and packaged frontend."""

from __future__ import annotations

import hmac
import json
import re
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

PREVIEW_LIMIT = 250_000
RUN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
ARTIFACT_SUFFIXES = {".html", ".json", ".jsonl", ".md", ".png", ".jpg", ".jpeg", ".webp", ".txt"}
ASSET_ROOT = Path(__file__).with_name("web_assets")


def _package_version() -> str:
    try:
        return version("scrape-gateway")
    except PackageNotFoundError:
        return "dev"


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


def create_console_routes(
    *,
    token: str = "",
    get_gateway: Callable[[], Any],
    get_config: Callable[[], GatewayConfig] = load_config,
    asset_root: Path = ASSET_ROOT,
) -> list:
    async def homepage(request: Request) -> Response:
        return HTMLResponse((asset_root / "index.html").read_text(encoding="utf-8"))

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
        try:
            result = await get_gateway().scrape(
                scrape_request,
                use_cache=use_cache,
                use_memory=use_cache,
            )
        except Exception as exc:  # noqa: BLE001 - return an operator-visible API error
            return _json_error(f"Scrape failed before producing a result: {exc}", 500)
        return JSONResponse(_result_payload(result))

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
        return JSONResponse({"runs": [_run_summary(report) for report in reports]})

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
        return JSONResponse({"report": report, "artifacts": _list_artifacts(root, run_id)})

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
) -> Starlette:
    return Starlette(
        routes=create_console_routes(
            token=token,
            get_gateway=get_gateway,
            get_config=get_config,
            asset_root=asset_root,
        )
    )
