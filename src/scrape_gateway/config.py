from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_FILENAMES = ["scrape-gateway.yml", "scrape-gateway.yaml"]


@dataclass(slots=True)
class ProviderConfig:
    name: str
    enabled: bool = True
    api_key_env: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CacheConfig:
    enabled: bool = True
    ttl_seconds: int = 86400  # 24h
    root: str = ".scrape-gateway/artifacts"


@dataclass(slots=True)
class StrategyConfig:
    mode: str = "cheapest_successful"
    max_cost_per_url: float | None = None


@dataclass(slots=True)
class GatewayConfig:
    providers: list[ProviderConfig] = field(default_factory=list)
    cache: CacheConfig = field(default_factory=CacheConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    memory_path: str = ".scrape-gateway/memory.sqlite"


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
    dotenv = path or Path(".env")
    if not dotenv.exists():
        return
    for line in dotenv.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'\"")
        if key and val:
            os.environ.setdefault(key, val)


def load_config(path: Path | str | None = None) -> GatewayConfig:
    _load_dotenv()

    if path:
        config_path = Path(path)
    else:
        config_path = None
        for name in CONFIG_FILENAMES:
            candidate = Path(name)
            if candidate.exists():
                config_path = candidate
                break

    if not config_path or not config_path.exists():
        return GatewayConfig()

    raw = yaml.safe_load(config_path.read_text()) or {}

    providers = []
    for p in raw.get("providers", []):
        if isinstance(p, str):
            providers.append(ProviderConfig(name=p))
        elif isinstance(p, dict):
            providers.append(
                ProviderConfig(
                    name=p["name"],
                    enabled=p.get("enabled", True),
                    api_key_env=p.get("api_key_env"),
                    options=p.get("options", {}),
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
        max_cost_per_url=strategy_raw.get("max_cost_per_url"),
    )

    return GatewayConfig(
        providers=providers,
        cache=cache,
        strategy=strategy,
        memory_path=raw.get("memory_path", ".scrape-gateway/memory.sqlite"),
    )
