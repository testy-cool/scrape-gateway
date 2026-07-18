from __future__ import annotations

from scrape_gateway.models import ScrapeResult
from scrape_gateway.telemetry import (
    TelemetryRecorder,
    result_summary,
    safe_metadata,
    summarize_telemetry,
)


def test_safe_metadata_redacts_nested_secret_key_variants() -> None:
    metadata = safe_metadata(
        {
            "safe_label": "keep-me",
            "OPENROUTER_API_KEY": "must-not-leak",
            "proxy-password": "must-not-leak",
            "sessionCookie": "must-not-leak",
            "nested": [
                {
                    "Authorization": "Bearer must-not-leak",
                    "safe_nested_label": "keep-me-too",
                }
            ],
        }
    )

    assert metadata == {
        "safe_label": "keep-me",
        "OPENROUTER_API_KEY": "<redacted>",
        "proxy-password": "<redacted>",
        "sessionCookie": "<redacted>",
        "nested": [
            {
                "Authorization": "<redacted>",
                "safe_nested_label": "keep-me-too",
            }
        ],
    }


def test_result_summary_records_screenshot_evidence_size() -> None:
    summary = result_summary(
        ScrapeResult(
            url="https://example.com",
            provider="browserless",
            success=True,
            screenshot=b"image-bytes",
        )
    )

    assert summary["screenshot_bytes"] == 11


def test_summarize_telemetry_aggregates_domains_diagnoses_costs_and_provider_hits() -> None:
    reports = [
        {
            "domain": "example.com",
            "success": True,
            "diagnosis": "success",
            "attempts": [{"provider": "raw_http", "cost": 0}],
            "final": {"provider": "raw_http"},
        },
        {
            "domain": "example.com",
            "success": False,
            "diagnosis": "validator_rejected",
            "attempts": [
                {"provider": "raw_http", "cost": 0},
                {"provider": "browserless", "cost": 2},
            ],
            "final": {"provider": "browserless"},
        },
        {
            "domain": "shop.example",
            "success": True,
            "diagnosis": "success",
            "attempts": [{"provider": "browserless", "cost": 3}],
            "final": {"provider": "browserless"},
        },
    ]

    summary = summarize_telemetry(reports)

    assert summary["runs"] == 3
    assert summary["successful_runs"] == 2
    assert summary["success_rate_pct"] == 66.7
    assert summary["average_attempt_count"] == 1.33
    assert summary["total_cost"] == 5.0
    assert summary["average_cost"] == 1.67
    assert summary["domains"] == [
        {"domain": "example.com", "runs": 2, "successes": 1, "success_rate_pct": 50.0},
        {"domain": "shop.example", "runs": 1, "successes": 1, "success_rate_pct": 100.0},
    ]
    assert summary["diagnoses"] == [
        {"diagnosis": "success", "count": 2, "share_pct": 66.7},
        {"diagnosis": "validator_rejected", "count": 1, "share_pct": 33.3},
    ]
    assert summary["providers"] == [
        {"provider": "browserless", "attempts": 2, "wins": 1, "hit_rate_pct": 50.0},
        {"provider": "raw_http", "attempts": 2, "wins": 1, "hit_rate_pct": 50.0},
    ]


def test_recorder_saves_final_visual_and_text_evidence_without_ai_evaluation(tmp_path) -> None:
    recorder = TelemetryRecorder(root=tmp_path / "runs")
    result = ScrapeResult(
        url="https://example.com",
        provider="browserless",
        success=True,
        html="<main>Captured page</main>",
        markdown="# Captured page",
        screenshot=b"\x89PNG\r\n\x1a\nimage",
    )

    artifacts = recorder.write_result_artifacts("run-visual", result)

    assert (tmp_path / "runs" / "run-visual" / "final.html").read_text() == result.html
    assert (tmp_path / "runs" / "run-visual" / "final.md").read_text() == result.markdown
    assert (tmp_path / "runs" / "run-visual" / "screenshot.png").read_bytes() == result.screenshot
    assert set(artifacts) == {"final_html", "final_markdown", "screenshot"}
