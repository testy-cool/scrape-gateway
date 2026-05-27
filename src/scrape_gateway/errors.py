from __future__ import annotations

from .models import FailureReason


def classify_failure(status_code: int | None, body: str | None = None) -> FailureReason | None:
    text = (body or "").lower()[:50_000]
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
    if "captcha" in text or "g-recaptcha" in text or "hcaptcha" in text:
        return FailureReason.CAPTCHA
    if "cloudflare" in text or "cf-chl" in text or "checking your browser" in text:
        return FailureReason.CLOUDFLARE
    if "enable javascript" in text or "requires javascript" in text:
        return FailureReason.JS_REQUIRED
    if "sign in" in text and "password" in text and len(text) < 8000:
        return FailureReason.LOGIN_REQUIRED
    return None


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
