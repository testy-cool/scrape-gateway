from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_FILENAMES = ["scrape-gateway.yml", "scrape-gateway.yaml"]

# Project root: the directory containing pyproject.toml / .env / scrape-gateway.yml.
# Resolved from this file's location (src/scrape_gateway/config.py → ../../..).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass(slots=True)
class ProviderConfig:
    name: str
    enabled: bool = True
    api_key_env: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("provider timeout_seconds must be positive")


@dataclass(slots=True)
class CacheConfig:
    enabled: bool = True
    ttl_seconds: int = 86400  # 24h
    root: str = ".scrape-gateway/artifacts"


@dataclass(slots=True)
class StrategyConfig:
    mode: str = "cheapest_successful"
    provider: str | None = None
    max_cost_per_url: float | None = None


@dataclass(slots=True)
class TelemetryConfig:
    enabled: bool = True
    root: str = ".scrape-gateway/runs"
    debug_artifacts: bool = False


@dataclass(slots=True)
class EvaluationConfig:
    mode: str = "off"
    model: str = "google/gemini-3.1-flash-lite"
    max_markdown_chars: int = 30_000
    include_screenshot: bool = True
    cache_root: str = ".scrape-gateway/evaluations"
    timeout_seconds: float = 60

    def __post_init__(self) -> None:
        if self.mode not in {"off", "audit"}:
            raise ValueError("evaluation mode must be 'off' or 'audit'")
        if self.max_markdown_chars <= 0:
            raise ValueError("evaluation max_markdown_chars must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("evaluation timeout_seconds must be positive")


@dataclass(slots=True)
class RequestConfig:
    default_timeout_seconds: float = 45

    def __post_init__(self) -> None:
        if self.default_timeout_seconds <= 0:
            raise ValueError("request default_timeout_seconds must be positive")


@dataclass(slots=True)
class GatewayConfig:
    providers: list[ProviderConfig] = field(default_factory=list)
    cache: CacheConfig = field(default_factory=CacheConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    request: RequestConfig = field(default_factory=RequestConfig)
    memory_path: str = ".scrape-gateway/memory.sqlite"
    recipes_root: str = "recipes"


_TTL_SUFFIXES = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_ttl(value: str | int) -> int:
    if isinstance(value, int):
        return value
    value = str(value).strip().lower()
    for suffix, multiplier in _TTL_SUFFIXES.items():
        if value.endswith(suffix):
            return int(value[: -len(suffix)]) * multiplier
    return int(value)


def _load_dotenv(path: Path | None = None) -> None:
    if path:
        dotenv = path
    elif Path(".env").exists():
        dotenv = Path(".env")
    elif (_PROJECT_ROOT / ".env").exists():
        dotenv = _PROJECT_ROOT / ".env"
    else:
        return
    if not dotenv.exists():
        return
    for line in dotenv.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'\"")
        if key and val and key not in os.environ:
            os.environ[key] = val


def _config_path(path: Path | str | None = None) -> Path:
    if path:
        return Path(path)
    for name in CONFIG_FILENAMES:
        # Check CWD first, then project root
        candidate = Path(name)
        if candidate.exists():
            return candidate
        candidate = _PROJECT_ROOT / name
        if candidate.exists():
            return candidate
    return Path(CONFIG_FILENAMES[0])


def _operator_settings_path(config_path: Path) -> Path:
    return config_path.parent / ".scrape-gateway" / "operator-settings.yml"


def save_operator_settings(
    settings: dict[str, Any], *, config_path: Path | str | None = None
) -> Path:
    """Atomically persist console-owned routing settings beside the base config."""

    path = _operator_settings_path(_config_path(config_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "request": {
            "default_timeout_seconds": settings["default_timeout_seconds"],
        },
        "evaluation": {
            "timeout_seconds": settings["evaluation_timeout_seconds"],
        },
        "providers": settings["providers"],
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    temporary.replace(path)
    return path


def load_config(path: Path | str | None = None) -> GatewayConfig:
    _load_dotenv()
    config_path = _config_path(path)

    raw = yaml.safe_load(config_path.read_text()) or {} if config_path.exists() else {}
    settings_path = _operator_settings_path(config_path)
    operator_raw = yaml.safe_load(settings_path.read_text()) or {} if settings_path.exists() else {}

    provider_overrides = {
        item["name"]: item
        for item in operator_raw.get("providers", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }

    providers = []
    for p in raw.get("providers", []):
        if isinstance(p, str):
            override = provider_overrides.pop(p, {})
            providers.append(
                ProviderConfig(
                    name=p,
                    enabled=override.get("enabled", True),
                    timeout_seconds=override.get("timeout_seconds"),
                )
            )
        elif isinstance(p, dict):
            merged = {**p, **provider_overrides.pop(p["name"], {})}
            providers.append(
                ProviderConfig(
                    name=merged["name"],
                    enabled=merged.get("enabled", True),
                    api_key_env=merged.get("api_key_env"),
                    options=merged.get("options", {}),
                    timeout_seconds=merged.get("timeout_seconds"),
                )
            )
    for name, override in provider_overrides.items():
        providers.append(
            ProviderConfig(
                name=name,
                enabled=override.get("enabled", True),
                timeout_seconds=override.get("timeout_seconds"),
            )
        )

    cache_raw = raw.get("cache", {})
    cache = CacheConfig(
        enabled=cache_raw.get("enabled", True),
        ttl_seconds=_parse_ttl(cache_raw.get("ttl", 86400)),
        root=cache_raw.get("root", ".scrape-gateway/artifacts"),
    )

    strategy_raw = raw.get("strategy", {})
    strategy = StrategyConfig(
        mode=strategy_raw.get("mode", "cheapest_successful"),
        provider=strategy_raw.get("provider"),
        max_cost_per_url=strategy_raw.get("max_cost_per_url"),
    )

    telemetry_raw = raw.get("telemetry", {})
    telemetry = TelemetryConfig(
        enabled=telemetry_raw.get("enabled", True),
        root=telemetry_raw.get("root", ".scrape-gateway/runs"),
        debug_artifacts=telemetry_raw.get("debug_artifacts", False),
    )

    evaluation_raw = raw.get("evaluation", {})
    evaluation_override = operator_raw.get("evaluation", {})
    evaluation_mode = evaluation_raw.get("mode", "off")
    if evaluation_mode is False:
        # PyYAML treats the common unquoted spelling `off` as a boolean.
        evaluation_mode = "off"
    evaluation = EvaluationConfig(
        mode=evaluation_mode,
        model=evaluation_raw.get("model", "google/gemini-3.1-flash-lite"),
        max_markdown_chars=evaluation_raw.get("max_markdown_chars", 30_000),
        include_screenshot=evaluation_raw.get("include_screenshot", True),
        cache_root=evaluation_raw.get("cache_root", ".scrape-gateway/evaluations"),
        timeout_seconds=evaluation_override.get(
            "timeout_seconds", evaluation_raw.get("timeout_seconds", 60)
        ),
    )

    request_raw = raw.get("request", {})
    request_override = operator_raw.get("request", {})
    request = RequestConfig(
        default_timeout_seconds=request_override.get(
            "default_timeout_seconds", request_raw.get("default_timeout_seconds", 45)
        )
    )

    recipes_root = Path(raw.get("recipes_root", "recipes"))
    if not recipes_root.is_absolute():
        recipes_root = config_path.parent / recipes_root

    return GatewayConfig(
        providers=providers,
        cache=cache,
        strategy=strategy,
        telemetry=telemetry,
        evaluation=evaluation,
        request=request,
        memory_path=raw.get("memory_path", ".scrape-gateway/memory.sqlite"),
        recipes_root=str(recipes_root),
    )
