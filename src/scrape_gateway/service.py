"""FastAPI service mode for Scrape Gateway."""

from __future__ import annotations

import base64
import hmac
import json
import re
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .models import ScrapeRequest
from .router import ScrapeGateway
from .telemetry import safe_metadata

_CACHE_KEY = re.compile(r"^[0-9a-f]{24}$")
OutputFormat = Literal["html", "markdown", "screenshot"]


class ServiceScrapeRequest(BaseModel):
    url: str = Field(min_length=1)
    country: str | None = None
    render_js: bool = False
    premium: bool = False
    formats: list[OutputFormat] = Field(default_factory=lambda: ["markdown"])
    use_cache: bool = True
    use_memory: bool = True


def _version() -> str:
    try:
        return version("scrape-gateway")
    except PackageNotFoundError:
        return "unknown"


def _gateway_accessor(
    gateway: ScrapeGateway | None,
    gateway_factory: Callable[[], ScrapeGateway] | None,
) -> Callable[[], ScrapeGateway]:
    current = gateway

    def get_gateway() -> ScrapeGateway:
        nonlocal current
        if gateway_factory is not None:
            return gateway_factory()
        if current is None:
            current = ScrapeGateway.from_config()
        return current

    return get_gateway


def health_payload(gateway: ScrapeGateway) -> dict:
    return {
        "status": "ok",
        "service": "scrape-gateway",
        "version": _version(),
        "providers": [provider.name for provider in gateway.providers],
    }


def create_app(
    gateway: ScrapeGateway | None = None,
    *,
    gateway_factory: Callable[[], ScrapeGateway] | None = None,
    token: str = "",
    route_prefix: str = "/v1",
    include_health: bool = True,
) -> FastAPI:
    """Create the HTTP API without eagerly constructing provider clients."""

    get_gateway = _gateway_accessor(gateway, gateway_factory)
    api = FastAPI(title="Scrape Gateway", version=_version())

    async def authorize(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        if not token:
            return
        scheme, _, credential = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(credential, token):
            raise HTTPException(
                status_code=401,
                detail="Missing or invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    dependencies = [Depends(authorize)]

    if include_health:

        @api.get("/health")
        async def health() -> dict:
            return health_payload(get_gateway())

    @api.post(f"{route_prefix}/scrape", dependencies=dependencies)
    async def scrape_page(payload: ServiceScrapeRequest) -> dict:
        formats = list(dict.fromkeys(payload.formats))
        gateway_instance = get_gateway()
        request = ScrapeRequest(
            url=payload.url,
            country=payload.country,
            render_js=payload.render_js,
            premium=payload.premium,
            screenshot="screenshot" in formats,
            output_format="markdown" if "markdown" in formats else "html",
        )
        result = await gateway_instance.scrape(
            request,
            use_cache=payload.use_cache,
            use_memory=payload.use_memory,
        )
        content = {}
        if "html" in formats:
            content["html"] = result.html
        if "markdown" in formats:
            content["markdown"] = result.markdown
        if "screenshot" in formats:
            content["screenshot"] = (
                base64.b64encode(result.screenshot).decode("ascii") if result.screenshot else None
            )
        return {
            "url": result.url,
            "cache_key": gateway_instance.cache.key_for_url(
                result.url, render_js=request.render_js
            ),
            "success": result.success,
            "provider": result.provider,
            "route": result.route,
            "status_code": result.status_code,
            "failure_reason": result.failure_reason.value if result.failure_reason else None,
            "error": result.error,
            "cost_units": result.cost_units,
            "content_validated": result.content_validated,
            "block_type": result.block_type,
            "validation_detail": result.validation_detail,
            "metadata": safe_metadata(result.metadata),
            "content": content,
        }

    @api.get(f"{route_prefix}/cache/{{url_hash}}", dependencies=dependencies)
    async def cache_entry(url_hash: str) -> dict:
        if not _CACHE_KEY.fullmatch(url_hash):
            raise HTTPException(status_code=404, detail="Cache entry not found")
        folder = get_gateway().cache.root / url_hash
        meta_path = folder / "meta.json"
        if not folder.is_dir() or not meta_path.is_file():
            raise HTTPException(status_code=404, detail="Cache entry not found")
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="Cache metadata is unreadable") from exc

        def read_text(name: str) -> str | None:
            path = folder / name
            return path.read_text(encoding="utf-8") if path.is_file() else None

        screenshot_path = folder / "screenshot.bin"
        screenshot = (
            base64.b64encode(screenshot_path.read_bytes()).decode("ascii")
            if screenshot_path.is_file()
            else None
        )
        return {
            "key": url_hash,
            "url": metadata.get("url"),
            "provider": metadata.get("provider"),
            "route": metadata.get("route"),
            "fetched_at": metadata.get("fetched_at"),
            "html": read_text("page.html"),
            "markdown": read_text("page.md"),
            "screenshot": screenshot,
        }

    @api.get(f"{route_prefix}/stats/{{domain}}", dependencies=dependencies)
    async def domain_stats(domain: str) -> dict:
        normalized = domain.lower().removeprefix("www.").rstrip(".")
        return {
            "domain": normalized,
            "providers": get_gateway().memory.provider_stats(f"https://{normalized}"),
        }

    return api


app = create_app()
