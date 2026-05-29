from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class FailureReason(str, Enum):
    TIMEOUT = "timeout"
    HTTP_403 = "http_403"
    HTTP_429 = "http_429"
    HTTP_5XX = "http_5xx"
    CAPTCHA = "captcha"
    CLOUDFLARE = "cloudflare"
    EMPTY_CONTENT = "empty_content"
    JS_REQUIRED = "js_required"
    LOGIN_REQUIRED = "login_required"
    PAYWALL = "paywall"
    PROXY_ERROR = "proxy_error"
    PROVIDER_ERROR = "provider_error"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class ScrapeRequest:
    url: str
    country: str | None = None
    render_js: bool = False
    premium: bool = False
    screenshot: bool = False
    mobile: bool = False
    wait_event: str | None = None  # domcontentloaded, load, networkidle
    wait_selector: str | None = None
    extra_wait_ms: int = 0
    block_ads: bool = False
    output_format: str = "html"  # html, markdown
    timeout_seconds: float = 45
    referer: str | None = None  # None = auto (Google search), "" = no referer
    skip_validation: bool = False
    headers: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ScrapeResult:
    url: str
    provider: str
    success: bool
    status_code: int | None = None
    html: str | None = None
    markdown: str | None = None
    screenshot: bytes | None = None
    failure_reason: FailureReason | None = None
    error: str | None = None
    cost_units: float = 0
    latency_ms: int | None = None
    route: str | None = None
    content_validated: bool | None = None
    block_type: str | None = None
    validation_detail: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


ProviderCapability = Literal["html", "markdown", "screenshot", "country", "render_js", "premium"]
