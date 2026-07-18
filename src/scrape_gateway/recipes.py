from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from .models import ScrapeRequest

_TTL_SUFFIXES = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _ttl_seconds(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    normalized = str(value).strip().lower()
    for suffix, multiplier in _TTL_SUFFIXES.items():
        if normalized.endswith(suffix):
            return int(normalized[: -len(suffix)]) * multiplier
    return int(normalized)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


@dataclass(slots=True)
class RecipeRoute:
    provider: str
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DomainRecipe:
    domain: str
    routes: list[RecipeRoute] = field(default_factory=list)
    validators: dict[str, Any] = field(default_factory=dict)
    failure_patterns: dict[str, Any] | list[str] = field(default_factory=dict)
    ttl_seconds: int | None = None

    @property
    def provider_names(self) -> list[str]:
        return [route.provider for route in self.routes]

    def route_settings(self, provider: str) -> dict[str, Any]:
        route = next((item for item in self.routes if item.provider == provider), None)
        return dict(route.settings) if route else {}

    @property
    def validation_kwargs(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if "min_text_chars" in self.validators:
            result["min_text_chars"] = int(self.validators["min_text_chars"])
        required = _string_list(self.validators.get("must_contain_any"))
        if required:
            result["must_contain_any"] = required
        forbidden = _string_list(self.validators.get("must_not_contain"))
        if isinstance(self.failure_patterns, dict):
            for patterns in self.failure_patterns.values():
                forbidden.extend(_string_list(patterns))
        else:
            forbidden.extend(_string_list(self.failure_patterns))
        if forbidden:
            result["must_not_contain"] = list(dict.fromkeys(forbidden))
        return result

    def apply_to_request(self, request: ScrapeRequest) -> None:
        request.metadata["domain_recipe"] = self.domain
        request.metadata["recipe_providers"] = self.provider_names
        if self.ttl_seconds is not None:
            request.metadata["recipe_ttl_seconds"] = self.ttl_seconds
        if not self.routes:
            return

        provider = self.routes[0].provider
        settings = self.route_settings(provider)
        aliases = {"country_code": "country"}
        request_fields = {
            "country",
            "render_js",
            "premium",
            "screenshot",
            "mobile",
            "wait_event",
            "wait_selector",
            "extra_wait_ms",
            "block_ads",
            "output_format",
            "timeout_seconds",
            "referer",
            "skip_validation",
        }
        for key, value in settings.items():
            field_name = aliases.get(key, key)
            if field_name in request_fields:
                setattr(request, field_name, value)
        scrape_tier = settings.get("scrape_tier")
        if isinstance(scrape_tier, str) and scrape_tier:
            request.metadata["start_tier"] = f"{provider}:{scrape_tier}"


class DomainRecipeStore:
    def __init__(self, root: str | Path = "recipes") -> None:
        self.root = Path(root)
        self._recipes = self._load()

    def _load(self) -> dict[str, DomainRecipe]:
        if not self.root.is_dir():
            return {}
        recipes: dict[str, DomainRecipe] = {}
        paths = sorted([*self.root.glob("*.yml"), *self.root.glob("*.yaml")])
        for path in paths:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict) or not isinstance(raw.get("domain"), str):
                raise ValueError(f"Recipe {path} must define a domain")
            routes = []
            for item in raw.get("routes", []):
                if not isinstance(item, dict) or not isinstance(item.get("provider"), str):
                    raise ValueError(f"Recipe route in {path} must define a provider")
                settings = item.get("settings", {})
                if not isinstance(settings, dict):
                    raise ValueError(f"Recipe route settings in {path} must be a mapping")
                routes.append(RecipeRoute(provider=item["provider"], settings=settings))
            domain = raw["domain"].lower().removeprefix("www.").rstrip(".")
            validators = raw.get("validators", {})
            if not isinstance(validators, dict):
                raise ValueError(f"Recipe validators in {path} must be a mapping")
            recipes[domain] = DomainRecipe(
                domain=domain,
                routes=routes,
                validators=validators,
                failure_patterns=raw.get("failure_patterns", {}),
                ttl_seconds=_ttl_seconds(raw.get("ttl")),
            )
        return recipes

    def for_url(self, url: str) -> DomainRecipe | None:
        host = (urlparse(url).hostname or "").lower().removeprefix("www.").rstrip(".")
        matches = [
            recipe
            for domain, recipe in self._recipes.items()
            if host == domain or host.endswith(f".{domain}")
        ]
        return max(matches, key=lambda recipe: len(recipe.domain), default=None)
