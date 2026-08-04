from __future__ import annotations

import hashlib
import ipaddress
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

from .memory import DomainMemory
from .paths import RUN_ID_PATTERN, safe_child

FEED_SCHEMA = "scrapingevals.sgw-observations/v1"
_PRIVATE_HOST_SUFFIXES = (
    ".example",
    ".home.arpa",
    ".internal",
    ".invalid",
    ".lan",
    ".local",
    ".localhost",
    ".onion",
    ".test",
)


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _package_version() -> str:
    try:
        return version("scrape-gateway")
    except PackageNotFoundError:
        return "unknown"


def _is_public_host(hostname: str) -> bool:
    lowered = hostname.lower().rstrip(".")
    if (
        not lowered
        or lowered == "localhost"
        or "." not in lowered
        or lowered.endswith(_PRIVATE_HOST_SUFFIXES)
    ):
        return False
    try:
        return ipaddress.ip_address(lowered).is_global
    except ValueError:
        return True


def _public_target(url: str, *, include_url_paths: bool) -> dict[str, Any] | None:
    parsed = urlsplit(url)
    hostname = parsed.hostname
    if parsed.scheme not in {"http", "https"} or not hostname or not _is_public_host(hostname):
        return None

    host = hostname.lower().rstrip(".")
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        return None
    if port and not (
        (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"

    has_path = parsed.path not in {"", "/"}
    public_path = parsed.path if include_url_paths else ""
    sanitized = urlunsplit(
        SplitResult(
            scheme=parsed.scheme,
            netloc=host,
            path=public_path,
            query="",
            fragment="",
        )
    )
    return {
        "domain": hostname.lower().removeprefix("www.").rstrip("."),
        "url": sanitized,
        "path_included": bool(include_url_paths and has_path),
        "path_redacted": bool(has_path and not include_url_paths),
        "query_redacted": bool(parsed.query or parsed.fragment),
    }


def _load_report(telemetry_root: Path, run_id: str) -> tuple[Path | None, dict[str, Any] | None]:
    try:
        run_dir = safe_child(telemetry_root, run_id, pattern=RUN_ID_PATTERN)
    except ValueError:
        return None, None
    report_path = run_dir / "report.json"
    if not report_path.is_file():
        return run_dir, None
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return run_dir, None
    if not isinstance(report, dict) or report.get("run_id") != run_id:
        return run_dir, None
    return run_dir, report


def _artifact_record(kind: str, path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    return {
        "kind": kind,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _artifact_records(run_dir: Path | None) -> list[dict[str, Any]]:
    if run_dir is None:
        return []
    candidates: list[tuple[str, Path]] = [
        ("final_html", run_dir / "final.html"),
        ("final_markdown", run_dir / "final.md"),
    ]
    for suffix in ("png", "jpg", "jpeg", "webp"):
        candidates.append(("screenshot", run_dir / f"screenshot.{suffix}"))

    artifacts: list[dict[str, Any]] = []
    seen_kinds: set[str] = set()
    for kind, path in candidates:
        if kind in seen_kinds or not path.is_file():
            continue
        try:
            artifacts.append(_artifact_record(kind, path))
        except OSError:
            continue
        seen_kinds.add(kind)
    return artifacts


def _clean_evaluation(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    checks: dict[str, Any] = {}
    raw_checks = value.get("checks")
    if isinstance(raw_checks, dict):
        for name, check in raw_checks.items():
            if isinstance(name, str) and isinstance(check, dict):
                result = check.get("result")
                if isinstance(result, str):
                    checks[name] = result

    result = {
        key: value.get(key)
        for key in (
            "status",
            "model",
            "prompt_version",
            "calibration_status",
            "verdict",
            "needs_human_review",
        )
    }
    result["checks"] = checks
    for key in (
        "page_type",
        "root_cause",
        "recommended_action",
        "input_modalities",
    ):
        result[key] = value.get(key)
    return result


def _clean_final(report: dict[str, Any] | None, last_attempt: dict[str, Any]) -> dict[str, Any]:
    final = report.get("final") if report else None
    if not isinstance(final, dict):
        final = {}
    return {
        "provider": final.get("provider", last_attempt["provider"]),
        "route": final.get("route", last_attempt["route"]),
        "success": bool(final.get("success", last_attempt["success"])),
        "status_code": final.get("status", last_attempt["status_code"]),
        "failure_reason": final.get("failure_reason", last_attempt["failure_reason"]),
        "block_type": final.get("block_type", last_attempt["block_type"]),
        "content_validated": final.get("content_validated"),
        "html_chars": final.get("chars"),
        "markdown_chars": final.get("markdown_chars"),
        "screenshot_bytes": final.get("screenshot_bytes"),
    }


def _attempt_record(
    row: dict[str, Any],
    target: dict[str, Any],
    *,
    source_instance_id: str,
) -> dict[str, Any]:
    run_id = str(row["run_id"])
    attempt_index = int(row["attempt_index"])
    ledger_id = int(row["id"])
    return {
        "event_id": f"{source_instance_id}:{ledger_id}",
        "ledger_id": ledger_id,
        "attempt_id": f"{run_id}:{attempt_index}",
        "run_id": run_id,
        "attempt_index": attempt_index,
        "recorded_at": row["recorded_at"],
        "target": target,
        "request_profile": {
            "country": row["country"],
            "render_js": bool(row["render_js"]),
            "premium": bool(row["premium"]),
            "mobile": bool(row["mobile"]),
            "screenshot": bool(row["screenshot"]),
        },
        "provider": row["provider"],
        "route": row["route"],
        "outcome": {
            "success": bool(row["success"]),
            "status_code": row["status_code"],
            "failure_reason": row["failure_reason"],
            "block_type": row["block_type"],
            "latency_ms": row["latency_ms"],
        },
        "cost": {
            "units": float(row["cost_units"]),
            "provenance": row["cost_provenance"],
        },
    }


def build_scrapingevals_feed(
    memory: DomainMemory,
    telemetry_root: str | Path,
    *,
    days: int = 30,
    after_ledger_id: int = 0,
    limit: int = 1_000,
    include_url_paths: bool = False,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    if days < 0:
        raise ValueError("days must be non-negative")
    if after_ledger_id < 0:
        raise ValueError("after_ledger_id must be non-negative")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    now = generated_at or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("generated_at must include a timezone")
    source_instance_id = memory.source_instance_id()
    params: list[Any] = [after_ledger_id]
    time_clause = ""
    if days:
        since = now.astimezone(UTC) - timedelta(days=days)
        time_clause = "and recorded_at >= ? and recorded_at <= ?"
        params.extend((_utc_timestamp(since), _utc_timestamp(now)))
    params.append(limit + 1)
    raw_rows = memory.conn.execute(
        f"""
        select id, run_id, attempt_index, recorded_at, domain, url, country,
               render_js, premium, mobile, screenshot, provider, route,
               cost_units, cost_provenance, success, status_code,
               failure_reason, block_type, latency_ms
        from attempt_ledger
        where id > ?
          {time_clause}
        order by id
        limit ?
        """,
        params,
    ).fetchall()
    has_more = len(raw_rows) > limit
    rows = raw_rows[:limit]
    through_ledger_id = int(rows[-1]["id"]) if rows else after_ledger_id

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    attempts: list[dict[str, Any]] = []
    excluded_private_attempts = 0
    for sqlite_row in rows:
        row = dict(sqlite_row)
        target = _public_target(row["url"], include_url_paths=include_url_paths)
        if target is None:
            excluded_private_attempts += 1
            continue
        grouped[str(row["run_id"])].append(row)
        attempts.append(
            _attempt_record(
                row,
                target,
                source_instance_id=source_instance_id,
            )
        )

    runs: list[dict[str, Any]] = []
    telemetry_path = Path(telemetry_root)
    for run_id, run_rows in grouped.items():
        first = run_rows[0]
        last = run_rows[-1]
        target = _public_target(first["url"], include_url_paths=include_url_paths)
        assert target is not None
        run_dir, report = _load_report(telemetry_path, run_id)
        success = (
            bool(report.get("success")) if report else any(bool(row["success"]) for row in run_rows)
        )
        runs.append(
            {
                "run_id": run_id,
                "recorded_at": first["recorded_at"],
                "started_at": report.get("started_at") if report else None,
                "finished_at": report.get("finished_at") if report else None,
                "elapsed_ms": report.get("elapsed_ms") if report else None,
                "target": target,
                "request_profile": {
                    "country": first["country"],
                    "render_js": bool(first["render_js"]),
                    "premium": bool(first["premium"]),
                    "mobile": bool(first["mobile"]),
                    "screenshot": bool(first["screenshot"]),
                },
                "attempt_ids": [f"{run_id}:{int(row['attempt_index'])}" for row in run_rows],
                "final": _clean_final(report, last),
                "diagnosis": {
                    "code": report.get("diagnosis") if report else None,
                    "useful": report.get("useful") if report else success,
                },
                "evaluation": _clean_evaluation(report.get("evaluation") if report else None),
                "artifacts": _artifact_records(run_dir),
                "publication": {
                    "evidence_class": "operational_observation",
                    "status": "review_required",
                    "comparable": False,
                },
            }
        )

    successful_attempts = sum(1 for attempt in attempts if attempt["outcome"]["success"])
    return {
        "schema": FEED_SCHEMA,
        "generated_at": _utc_timestamp(now),
        "source": {
            "name": "scrape-gateway",
            "version": _package_version(),
            "instance_id": source_instance_id,
        },
        "selection": {
            "days": days,
            "include_url_paths": include_url_paths,
        },
        "privacy": {
            "private_targets": "excluded",
            "url_credentials": "removed",
            "url_query_and_fragment": "removed",
            "url_paths": "included" if include_url_paths else "removed",
            "headers_and_metadata": "omitted",
            "content_and_local_paths": "omitted",
        },
        "cursor": {
            "after_ledger_id": after_ledger_id,
            "through_ledger_id": through_ledger_id,
            "has_more": has_more,
        },
        "summary": {
            "runs": len(runs),
            "attempts": len(attempts),
            "successful_runs": sum(1 for run in runs if run["final"]["success"]),
            "successful_attempts": successful_attempts,
            "failed_attempts": len(attempts) - successful_attempts,
            "excluded_private_attempts": excluded_private_attempts,
            "cost_units": sum(attempt["cost"]["units"] for attempt in attempts),
        },
        "runs": runs,
        "attempts": attempts,
    }


def write_scrapingevals_feed(feed: dict[str, Any], output: str | Path) -> Path:
    path = Path(output)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(feed, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path
