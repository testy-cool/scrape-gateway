from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

from .provider import ProviderAdapter

EXTENSIONS_DIR = Path("~/.config/scrape-gateway/providers").expanduser()


def _builtin_providers() -> dict[str, type[ProviderAdapter]]:
    module = importlib.import_module(".providers", package="scrape_gateway")
    result = {}
    for attr_name in getattr(module, "__all__", []):
        cls = getattr(module, attr_name, None)
        if cls and isinstance(cls, type) and issubclass(cls, ProviderAdapter):
            result[cls.name] = cls
    return result


def _entrypoint_providers() -> dict[str, type[ProviderAdapter]]:
    from importlib.metadata import entry_points

    result = {}
    for ep in entry_points(group="scrape_gateway.providers"):
        try:
            cls = ep.load()
            if isinstance(cls, type) and issubclass(cls, ProviderAdapter):
                result[cls.name] = cls
        except Exception:  # noqa: BLE001
            print(f"  [extensions] failed to load entry point: {ep.name}", file=sys.stderr)
    return result


def _local_providers() -> dict[str, type[ProviderAdapter]]:
    if not EXTENSIONS_DIR.is_dir():
        return {}
    result = {}
    for f in sorted(EXTENSIONS_DIR.glob("*.py")):
        if f.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"sg_ext_{f.stem}", f)
            if not spec or not spec.loader:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for attr_name in dir(mod):
                obj = getattr(mod, attr_name)
                if (
                    isinstance(obj, type)
                    and issubclass(obj, ProviderAdapter)
                    and obj is not ProviderAdapter
                    and hasattr(obj, "name")
                ):
                    result[obj.name] = obj
        except Exception as exc:  # noqa: BLE001
            print(f"  [extensions] failed to load {f.name}: {exc}", file=sys.stderr)
    return result


def discover_providers() -> dict[str, type[ProviderAdapter]]:
    """Find all provider classes: built-in, entry points, then local directory."""
    providers = _builtin_providers()
    providers.update(_entrypoint_providers())
    providers.update(_local_providers())
    return providers
