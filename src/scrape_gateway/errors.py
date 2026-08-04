from __future__ import annotations

from .models import FailureReason
from .validators import find_block_signature

_BLOCK_FAILURE_REASONS = {
    "cloudflare": FailureReason.CLOUDFLARE,
    "captcha": FailureReason.CAPTCHA,
    "js_shell": FailureReason.JS_REQUIRED,
    "login_wall": FailureReason.LOGIN_REQUIRED,
    "generic_error": FailureReason.PROVIDER_ERROR,
}

_PROVIDER_AUTH_FAILURE_MARKERS = (
    "authentication failed",
    "invalid api key",
    "invalid bearer token",
    "invalid credentials",
    "invalid token",
)


def classify_failure(status_code: int | None, body: str | None = None) -> FailureReason | None:
    if status_code == 407:
        return FailureReason.PROXY_ERROR
    if status_code == 403:
        return FailureReason.HTTP_403
    if status_code == 429:
        return FailureReason.HTTP_429
    if status_code and status_code >= 500:
        return FailureReason.HTTP_5XX
    if not body or len(body.strip()) < 80:
        return FailureReason.EMPTY_CONTENT
    block_match = find_block_signature(body)
    if block_match:
        return _BLOCK_FAILURE_REASONS.get(block_match[0], FailureReason.UNKNOWN)
    return None


def classify_provider_failure(
    status_code: int | None,
    body: str | None = None,
) -> FailureReason | None:
    normalized_body = (body or "").lower()
    if status_code == 401 or any(
        marker in normalized_body for marker in _PROVIDER_AUTH_FAILURE_MARKERS
    ):
        return FailureReason.PROVIDER_UNAVAILABLE
    return classify_failure(status_code, body)


def classify_exception(exc: Exception) -> FailureReason:
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    combined = f"{name} {message}"

    if "timeout" in combined or "timed out" in combined:
        return FailureReason.TIMEOUT
    if (
        "407" in combined
        or "proxy authentication" in combined
        or "proxyauthrequired" in combined
        or "proxyconnect" in combined
        or "proxyerror" in combined
    ):
        return FailureReason.PROXY_ERROR
    return FailureReason.UNKNOWN
