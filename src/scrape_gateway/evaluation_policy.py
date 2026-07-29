from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .models import ScrapeResult

SELECTIVE_GATE_VERSION = "selective-v1"
MIN_TRUSTED_HTML_CHARS = 8_192
MIN_TRUSTED_MARKDOWN_CHARS = 1_500
SCRIPT_TO_TEXT_AMBIGUITY_RATIO = 20.0
RICH_BLOCK_MARKDOWN_CHARS = 8_192
RICH_BLOCK_MAX_SCRIPT_TO_TEXT_RATIO = 5.0

_SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)
_NON_TEXT_RE = re.compile(
    r"<script\b[^>]*>.*?</script\s*>|<style\b[^>]*>.*?</style\s*>|<[^>]+>",
    re.IGNORECASE | re.DOTALL,
)
_PASSWORD_INPUT_RE = re.compile(
    r"<input\b[^>]*\btype\s*=\s*[\"']?password\b",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class SelectiveEvaluationDecision:
    call_model: bool
    reason: str
    signals: dict[str, Any]


def _runtime_signals(result: ScrapeResult) -> dict[str, Any]:
    html = result.html or ""
    markdown = result.markdown or ""
    scripts = "".join(_SCRIPT_RE.findall(html))
    visible_text = _WHITESPACE_RE.sub(" ", _NON_TEXT_RE.sub(" ", html)).strip()
    script_to_text_ratio = len(scripts) / max(len(visible_text), 1)
    return {
        "success": result.success,
        "status_code": result.status_code,
        "block_type": result.block_type,
        "html_chars": len(html),
        "markdown_chars": len(markdown),
        "visible_text_chars": len(visible_text),
        "script_chars": len(scripts),
        "script_to_text_ratio": round(script_to_text_ratio, 4),
        "has_password_input": bool(_PASSWORD_INPUT_RE.search(html)),
    }


def selective_evaluation_decision(result: ScrapeResult) -> SelectiveEvaluationDecision:
    """Decide whether deterministic evidence is ambiguous enough to justify an AI audit."""

    signals = _runtime_signals(result)
    if result.success:
        if signals["html_chars"] < MIN_TRUSTED_HTML_CHARS:
            return SelectiveEvaluationDecision(True, "thin_html", signals)
        if signals["markdown_chars"] < MIN_TRUSTED_MARKDOWN_CHARS:
            return SelectiveEvaluationDecision(True, "thin_markdown", signals)
        if signals["script_to_text_ratio"] >= SCRIPT_TO_TEXT_AMBIGUITY_RATIO:
            return SelectiveEvaluationDecision(True, "script_dominated", signals)
        if signals["has_password_input"]:
            return SelectiveEvaluationDecision(True, "password_form", signals)
        return SelectiveEvaluationDecision(False, "deterministic_pass", signals)

    content_rich_block = (
        result.status_code is not None
        and 200 <= result.status_code < 400
        and result.block_type is not None
        and signals["markdown_chars"] >= RICH_BLOCK_MARKDOWN_CHARS
        and signals["script_to_text_ratio"] < RICH_BLOCK_MAX_SCRIPT_TO_TEXT_RATIO
    )
    if content_rich_block:
        return SelectiveEvaluationDecision(True, "content_rich_block", signals)
    return SelectiveEvaluationDecision(False, "deterministic_fail", signals)


def selective_gate_description() -> dict[str, Any]:
    return {
        "version": SELECTIVE_GATE_VERSION,
        "runtime_signals": [
            "deterministic verdict",
            "status code",
            "matched block type",
            "HTML length",
            "Markdown length",
            "script-to-visible-text ratio",
            "password input presence",
        ],
        "rules": [
            (
                "Audit a deterministic pass when HTML is under 8192 characters, Markdown is "
                "under 1500 characters, scripts are at least 20 times the visible text, or a "
                "password input is present."
            ),
            (
                "Audit a deterministic block only when it is a 2xx/3xx response with a matched "
                "block type, at least 8192 Markdown characters, and a script-to-visible-text "
                "ratio below 5."
            ),
            "Otherwise keep the deterministic verdict and skip the model call.",
        ],
        "thresholds": {
            "min_trusted_html_chars": MIN_TRUSTED_HTML_CHARS,
            "min_trusted_markdown_chars": MIN_TRUSTED_MARKDOWN_CHARS,
            "script_to_text_ambiguity_ratio": SCRIPT_TO_TEXT_AMBIGUITY_RATIO,
            "rich_block_markdown_chars": RICH_BLOCK_MARKDOWN_CHARS,
            "rich_block_max_script_to_text_ratio": RICH_BLOCK_MAX_SCRIPT_TO_TEXT_RATIO,
        },
    }
