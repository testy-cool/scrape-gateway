from __future__ import annotations

from scrape_gateway.telemetry import safe_metadata


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
