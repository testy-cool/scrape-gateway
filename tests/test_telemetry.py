from __future__ import annotations

from scrape_gateway.models import ScrapeResult
from scrape_gateway.telemetry import TelemetryRecorder, result_summary, safe_metadata


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
