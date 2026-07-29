from __future__ import annotations

import pytest

from scrape_gateway.models import ScrapeResult


def _result(
    *,
    success: bool = True,
    status_code: int = 200,
    html: str,
    markdown: str,
    block_type: str | None = None,
) -> ScrapeResult:
    return ScrapeResult(
        url="https://example.com",
        provider="fixture",
        success=success,
        status_code=status_code,
        html=html,
        markdown=markdown,
        block_type=block_type,
        content_validated=success,
    )


@pytest.mark.parametrize(
    ("result", "expected_call", "expected_reason"),
    [
        (
            _result(
                html="<html><main>" + ("Useful article text. " * 600) + "</main></html>",
                markdown="Useful article text. " * 600,
            ),
            False,
            "deterministic_pass",
        ),
        (
            _result(
                html="<html><main>" + ("Partial. " * 100) + "</main></html>",
                markdown="Partial. " * 100,
            ),
            True,
            "thin_html",
        ),
        (
            _result(
                html=(
                    "<html><main>"
                    + ("Visible paywall text. " * 120)
                    + "</main><script>"
                    + ("window.__state__ = {}; " * 3_000)
                    + "</script></html>"
                ),
                markdown="Visible paywall text. " * 120,
            ),
            True,
            "script_dominated",
        ),
        (
            _result(
                html=(
                    "<html><form><input type='password'>"
                    + ("Sign in form content. " * 500)
                    + "</form></html>"
                ),
                markdown="Sign in form content. " * 500,
            ),
            True,
            "password_form",
        ),
        (
            _result(
                success=False,
                html="<html><article>" + ("CAPTCHA documentation. " * 700) + "</article></html>",
                markdown="CAPTCHA documentation. " * 700,
                block_type="captcha",
            ),
            True,
            "content_rich_block",
        ),
        (
            _result(
                success=False,
                html="<html><main>Verify you are human.</main></html>",
                markdown="Verify you are human.",
                block_type="captcha",
            ),
            False,
            "deterministic_fail",
        ),
        (
            _result(
                success=False,
                html=(
                    "<html><main>"
                    + ("Challenge page. " * 700)
                    + "</main><script>"
                    + ("challenge(); " * 8_000)
                    + "</script></html>"
                ),
                markdown="Challenge page. " * 700,
                block_type="captcha",
            ),
            False,
            "deterministic_fail",
        ),
    ],
)
def test_selective_gate_uses_runtime_ambiguity_signals(
    result: ScrapeResult,
    expected_call: bool,
    expected_reason: str,
) -> None:
    from scrape_gateway.evaluation_policy import selective_evaluation_decision

    decision = selective_evaluation_decision(result)

    assert decision.call_model is expected_call
    assert decision.reason == expected_reason
    assert set(decision.signals) == {
        "success",
        "status_code",
        "block_type",
        "html_chars",
        "markdown_chars",
        "visible_text_chars",
        "script_chars",
        "script_to_text_ratio",
        "has_password_input",
    }
