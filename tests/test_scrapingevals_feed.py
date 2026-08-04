from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from typer.testing import CliRunner

from scrape_gateway.cli import app
from scrape_gateway.memory import DomainMemory
from scrape_gateway.models import AttemptLedgerEntry, FailureReason, ScrapeRequest
from scrape_gateway.scrapingevals import build_scrapingevals_feed

runner = CliRunner()


def test_scrapingevals_command_exports_privacy_safe_passive_feed(tmp_path) -> None:
    memory_path = tmp_path / "memory.sqlite"
    telemetry_root = tmp_path / "runs"
    output_path = tmp_path / "scrapingevals" / "latest.json"
    output_path.parent.mkdir()
    memory = DomainMemory(memory_path)
    run_id = "1234567890abcdef"
    target_url = "https://user:password@www.example.com/private/path?token=secret#section"
    recorded_at = datetime.now(UTC)
    memory.record_attempt_ledger(
        run_id,
        ScrapeRequest(
            target_url,
            country="RO",
            render_js=True,
            screenshot=True,
        ),
        [
            AttemptLedgerEntry(
                provider="scrapedrive",
                route="scrapedrive:standard",
                cost_units=1,
                cost_provenance="estimated",
                success=False,
                latency_ms=120,
                status_code=403,
                failure_reason=FailureReason.HTTP_403,
                block_type="cloudflare",
            ),
            AttemptLedgerEntry(
                provider="scrapedrive",
                route="scrapedrive:advanced",
                cost_units=5,
                cost_provenance="exact",
                success=True,
                latency_ms=450,
                status_code=200,
                failure_reason=None,
                block_type=None,
            ),
        ],
        recorded_at=recorded_at,
    )

    run_dir = telemetry_root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "final.html").write_text("<main>public evidence</main>", encoding="utf-8")
    (run_dir / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\npublic-image")
    (run_dir / "report.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "started_at": recorded_at.isoformat(),
                "finished_at": recorded_at.isoformat(),
                "elapsed_ms": 700,
                "url": target_url,
                "success": True,
                "useful": True,
                "diagnosis": "success",
                "request": {
                    "metadata": {
                        "authorization": "Bearer do-not-export",
                        "evaluation_goal": "private goal",
                    }
                },
                "final": {
                    "provider": "scrapedrive",
                    "success": True,
                    "status": 200,
                    "route": "scrapedrive:advanced",
                    "cost": 6,
                    "failure_reason": None,
                    "block_type": None,
                    "chars": 1234,
                    "markdown_chars": 456,
                    "screenshot_bytes": 20,
                    "content_validated": True,
                    "metadata": {"token": "do-not-export"},
                },
                "evaluation": {
                    "status": "completed",
                    "model": "google/gemini-test",
                    "prompt_version": "scrape-usability-v2",
                    "calibration_status": "uncalibrated_audit",
                    "verdict": "pass",
                    "needs_human_review": False,
                    "checks": {
                        "access": {
                            "result": "pass",
                            "evidence": "private page excerpt",
                        }
                    },
                    "page_type": "listing_page",
                    "root_cause": "none",
                    "recommended_action": "accept",
                    "input_modalities": ["markdown", "screenshot"],
                    "critique": "do not export prose",
                    "artifacts": {"/absolute/private/path": "do-not-export"},
                    "generation_id": "private-generation-id",
                },
            }
        ),
        encoding="utf-8",
    )

    config = SimpleNamespace(
        memory_path=str(memory_path),
        telemetry=SimpleNamespace(root=str(telemetry_root)),
    )
    with patch("scrape_gateway.config.load_config", return_value=config):
        result = runner.invoke(
            app,
            ["scrapingevals", "--out", str(output_path), "--days", "30"],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "scrapingevals.sgw-observations/v1"
    assert payload["source"]["name"] == "scrape-gateway"
    assert payload["source"]["version"]
    assert payload["selection"] == {
        "days": 30,
        "include_url_paths": False,
    }
    assert payload["summary"] == {
        "runs": 1,
        "attempts": 2,
        "successful_runs": 1,
        "successful_attempts": 1,
        "failed_attempts": 1,
        "excluded_private_attempts": 0,
        "cost_units": 6.0,
    }

    assert payload["runs"] == [
        {
            "run_id": run_id,
            "recorded_at": recorded_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "started_at": recorded_at.isoformat(),
            "finished_at": recorded_at.isoformat(),
            "elapsed_ms": 700,
            "target": {
                "domain": "example.com",
                "url": "https://www.example.com",
                "path_included": False,
                "path_redacted": True,
                "query_redacted": True,
            },
            "request_profile": {
                "country": "RO",
                "render_js": True,
                "premium": False,
                "mobile": False,
                "screenshot": True,
            },
            "attempt_ids": [
                f"{run_id}:1",
                f"{run_id}:2",
            ],
            "final": {
                "provider": "scrapedrive",
                "route": "scrapedrive:advanced",
                "success": True,
                "status_code": 200,
                "failure_reason": None,
                "block_type": None,
                "content_validated": True,
                "html_chars": 1234,
                "markdown_chars": 456,
                "screenshot_bytes": 20,
            },
            "diagnosis": {
                "code": "success",
                "useful": True,
            },
            "evaluation": {
                "status": "completed",
                "model": "google/gemini-test",
                "prompt_version": "scrape-usability-v2",
                "calibration_status": "uncalibrated_audit",
                "verdict": "pass",
                "needs_human_review": False,
                "checks": {"access": "pass"},
                "page_type": "listing_page",
                "root_cause": "none",
                "recommended_action": "accept",
                "input_modalities": ["markdown", "screenshot"],
            },
            "artifacts": [
                {
                    "kind": "final_html",
                    "bytes": 28,
                    "sha256": "8d2e4a60044cb5e0484ff3669b2271124079d0c4d5ea26a69aaae6789fd90040",
                },
                {
                    "kind": "screenshot",
                    "bytes": 20,
                    "sha256": "2d0059e10a1109145067a95b4f1fb3a3dfe93118d3ac98b1f479522f6d1ad19d",
                },
            ],
            "publication": {
                "evidence_class": "operational_observation",
                "status": "review_required",
                "comparable": False,
            },
        }
    ]
    assert payload["attempts"][0]["outcome"]["failure_reason"] == "http_403"
    assert payload["attempts"][1]["cost"] == {
        "units": 5.0,
        "provenance": "exact",
    }
    serialized = output_path.read_text(encoding="utf-8")
    for secret in (
        "password",
        "token=secret",
        "Bearer do-not-export",
        "private page excerpt",
        "do not export prose",
        "/absolute/private/path",
        "private-generation-id",
    ):
        assert secret not in serialized


def test_feed_excludes_non_public_targets_without_leaking_hostnames(tmp_path) -> None:
    memory = DomainMemory(tmp_path / "memory.sqlite")
    recorded_at = datetime(2026, 7, 30, 1, tzinfo=UTC)
    targets = [
        "http://127.0.0.1/admin",
        "https://service.internal/private",
        "https://customer-name.example/account",
        "https://staging.invalid/debug",
        "http://hidden-service.onion/",
    ]
    for index, url in enumerate(targets, start=1):
        memory.record_attempt_ledger(
            f"{index:016x}",
            ScrapeRequest(url),
            [
                AttemptLedgerEntry(
                    provider="raw_http",
                    route="raw_http",
                    cost_units=0,
                    cost_provenance="estimated",
                    success=True,
                    latency_ms=10,
                    status_code=200,
                    failure_reason=None,
                    block_type=None,
                )
            ],
            recorded_at=recorded_at,
        )

    feed = build_scrapingevals_feed(
        memory,
        tmp_path / "runs",
        generated_at=recorded_at,
    )

    assert feed["summary"] == {
        "runs": 0,
        "attempts": 0,
        "successful_runs": 0,
        "successful_attempts": 0,
        "failed_attempts": 0,
        "excluded_private_attempts": len(targets),
        "cost_units": 0,
    }
    assert feed["runs"] == []
    assert feed["attempts"] == []
    serialized = json.dumps(feed)
    for hostname in (
        "127.0.0.1",
        "service.internal",
        "customer-name.example",
        "staging.invalid",
        "hidden-service.onion",
    ):
        assert hostname not in serialized


def test_scrapingevals_command_exports_an_incremental_idempotent_cursor(tmp_path) -> None:
    memory_path = tmp_path / "memory.sqlite"
    memory = DomainMemory(memory_path)
    recorded_at = datetime.now(UTC)
    for index in range(1, 4):
        memory.record_attempt_ledger(
            f"{index:016x}",
            ScrapeRequest(f"https://example.com/page-{index}"),
            [
                AttemptLedgerEntry(
                    provider="raw_http",
                    route="raw_http",
                    cost_units=0,
                    cost_provenance="estimated",
                    success=True,
                    latency_ms=index * 10,
                    status_code=200,
                    failure_reason=None,
                    block_type=None,
                )
            ],
            recorded_at=recorded_at,
        )

    config = SimpleNamespace(
        memory_path=str(memory_path),
        telemetry=SimpleNamespace(root=str(tmp_path / "runs")),
    )
    first_output = tmp_path / "first.json"
    second_output = tmp_path / "second.json"
    with patch("scrape_gateway.config.load_config", return_value=config):
        first = runner.invoke(
            app,
            [
                "scrapingevals",
                "--out",
                str(first_output),
                "--days",
                "0",
                "--after-ledger-id",
                "1",
                "--limit",
                "1",
            ],
        )
        second = runner.invoke(
            app,
            [
                "scrapingevals",
                "--out",
                str(second_output),
                "--days",
                "0",
                "--after-ledger-id",
                "2",
                "--limit",
                "1",
            ],
        )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    first_feed = json.loads(first_output.read_text(encoding="utf-8"))
    second_feed = json.loads(second_output.read_text(encoding="utf-8"))
    source_id = first_feed["source"]["instance_id"]
    assert re.fullmatch(r"[0-9a-f]{32}", source_id)
    assert second_feed["source"]["instance_id"] == source_id
    assert first_feed["cursor"] == {
        "after_ledger_id": 1,
        "through_ledger_id": 2,
        "has_more": True,
    }
    assert second_feed["cursor"] == {
        "after_ledger_id": 2,
        "through_ledger_id": 3,
        "has_more": False,
    }
    assert first_feed["attempts"][0]["ledger_id"] == 2
    assert first_feed["attempts"][0]["event_id"] == f"{source_id}:2"
    assert second_feed["attempts"][0]["ledger_id"] == 3
    assert second_feed["attempts"][0]["event_id"] == f"{source_id}:3"
