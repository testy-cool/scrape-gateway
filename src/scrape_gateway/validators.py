from __future__ import annotations

from dataclasses import dataclass, field

BLOCK_SIGNATURES: dict[str, list[str]] = {
    "cloudflare": [
        "checking your browser",
        "cf-chl",
        "turnstile",
        "ray id:",
        "cloudflare to restrict access",
        "enable javascript and cookies to continue",
    ],
    "akamai": [
        "access denied",
        "reference #",
        "akamai ghost",
    ],
    "captcha": [
        "g-recaptcha",
        "hcaptcha",
        "solve the captcha",
        "verify you are human",
        "prove you're not a robot",
    ],
    "js_shell": [
        "enable javascript",
        "requires javascript",
        "javascript is required",
        "please turn javascript on",
        "you need to enable javascript",
    ],
    "login_wall": [
        "sign in to continue",
        "log in to continue",
        "create an account",
        "please log in",
    ],
    "consent_wall": [
        "accept cookies to continue",
        "consent to continue",
        "we use cookies",
        "cookie preferences",
    ],
}


@dataclass(slots=True)
class ValidationResult:
    passed: bool
    checks_run: list[str] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)
    block_type: str | None = None
    detail: str | None = None


def validate_content(
    html: str | None,
    *,
    min_text_chars: int = 80,
    must_not_contain: list[str] | None = None,
    must_contain_any: list[str] | None = None,
) -> ValidationResult:
    checks_run: list[str] = []
    checks_failed: list[str] = []
    block_type: str | None = None
    detail: str | None = None

    text = (html or "").lower()[:100_000]

    checks_run.append("min_text_chars")
    if len(text.strip()) < min_text_chars:
        checks_failed.append("min_text_chars")
        return ValidationResult(
            passed=False,
            checks_run=checks_run,
            checks_failed=checks_failed,
            block_type="empty_content",
            detail=f"Content too short: {len(text.strip())} < {min_text_chars}",
        )

    checks_run.append("block_signatures")
    for sig_type, patterns in BLOCK_SIGNATURES.items():
        for pattern in patterns:
            if pattern in text:
                checks_failed.append("block_signatures")
                block_type = sig_type
                detail = f"Matched {sig_type} signature: '{pattern}'"
                return ValidationResult(
                    passed=False,
                    checks_run=checks_run,
                    checks_failed=checks_failed,
                    block_type=block_type,
                    detail=detail,
                )

    if must_not_contain:
        checks_run.append("must_not_contain")
        for phrase in must_not_contain:
            if phrase.lower() in text:
                checks_failed.append("must_not_contain")
                return ValidationResult(
                    passed=False,
                    checks_run=checks_run,
                    checks_failed=checks_failed,
                    detail=f"Contains forbidden phrase: '{phrase}'",
                )

    if must_contain_any:
        checks_run.append("must_contain_any")
        if not any(phrase.lower() in text for phrase in must_contain_any):
            checks_failed.append("must_contain_any")
            return ValidationResult(
                passed=False,
                checks_run=checks_run,
                checks_failed=checks_failed,
                detail=f"Missing required phrases: {must_contain_any}",
            )

    return ValidationResult(passed=True, checks_run=checks_run, checks_failed=checks_failed)
