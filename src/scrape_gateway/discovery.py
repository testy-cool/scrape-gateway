from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

from .provider import ProviderAdapter

EXTENSIONS_DIR = Path("~/.config/scrape-gateway/providers").expanduser()
COMMAND_EXTENSIONS_DIR = Path("~/.config/scrape-gateway/commands").expanduser()


def _check_deps(cls: type[ProviderAdapter]) -> bool:
    """Check if a provider's dependencies are installed. Prompt to install if not."""
    deps = getattr(cls, "install_requires", [])
    if not deps:
        return True

    missing = []
    for dep in deps:
        pkg_name = dep.split("[")[0].split(">")[0].split("<")[0].split("=")[0].strip()
        import_name = pkg_name.replace("-", "_")
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append(dep)

    if not missing:
        return True

    print(f"  [extensions] {cls.name} needs: {', '.join(missing)}", file=sys.stderr)
    if not sys.stdin.isatty():
        print(f"  [extensions] skipping {cls.name} (non-interactive)", file=sys.stderr)
        return False

    try:
        answer = input("  Install into sgw's environment? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False

    if answer and answer not in ("y", "yes"):
        return False

    result = subprocess.run(
        ["uv", "pip", "install", "--python", sys.executable, *missing],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print(f"  [extensions] installed {', '.join(missing)}", file=sys.stderr)
        for dep in missing:
            pkg_name = dep.split("[")[0].split(">")[0].split("<")[0].split("=")[0].strip()
            import_name = pkg_name.replace("-", "_")
            importlib.invalidate_caches()
            try:
                importlib.import_module(import_name)
            except ImportError:
                pass
        return True

    print(f"  [extensions] install failed: {result.stderr.strip()}", file=sys.stderr)
    return False


def _entrypoint_providers() -> dict[str, type[ProviderAdapter]]:
    from importlib.metadata import entry_points

    result = {}
    for ep in entry_points(group="scrape_gateway.providers"):
        try:
            cls = ep.load()
            if isinstance(cls, type) and issubclass(cls, ProviderAdapter):
                if _check_deps(cls):
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
                    if _check_deps(obj):
                        result[obj.name] = obj
        except Exception as exc:  # noqa: BLE001
            print(f"  [extensions] failed to load {f.name}: {exc}", file=sys.stderr)
    return result


def discover_providers() -> dict[str, type[ProviderAdapter]]:
    """Find all provider classes: entry points first, then local directory."""
    providers = _entrypoint_providers()
    providers.update(_local_providers())
    return providers


def discover_providers_with_sources() -> dict[str, tuple[type[ProviderAdapter], str]]:
    """Like discover_providers, but also returns the source of each provider."""
    result = {}
    for name, cls in _entrypoint_providers().items():
        result[name] = (cls, "package")
    for name, cls in _local_providers().items():
        result[name] = (cls, str(EXTENSIONS_DIR))
    return result


def _entrypoint_command_extensions(app: Any) -> dict[str, str]:
    """Load command extensions published as package entry points."""
    from importlib.metadata import entry_points

    loaded: dict[str, str] = {}
    for ep in entry_points(group="scrape_gateway.commands"):
        try:
            register = ep.load()
            if not callable(register):
                print(
                    f"  [extensions] command entry point {ep.name} is not callable",
                    file=sys.stderr,
                )
                continue
            register(app)
            loaded[ep.name] = "package"
        except Exception as exc:  # noqa: BLE001
            print(
                f"  [extensions] failed to load command entry point {ep.name}: {exc}",
                file=sys.stderr,
            )
    return loaded


def _local_command_extensions(app: Any) -> dict[str, str]:
    """Load command extensions from ~/.config/scrape-gateway/commands."""
    if not COMMAND_EXTENSIONS_DIR.is_dir():
        return {}
    loaded: dict[str, str] = {}
    for f in sorted(COMMAND_EXTENSIONS_DIR.glob("*.py")):
        if f.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"sg_cmd_ext_{f.stem}", f)
            if not spec or not spec.loader:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            register = getattr(mod, "register", None)
            if not callable(register):
                print(
                    f"  [extensions] command extension {f.name} has no register(app) function",
                    file=sys.stderr,
                )
                continue
            register(app)
            loaded[f.stem] = str(COMMAND_EXTENSIONS_DIR)
        except Exception as exc:  # noqa: BLE001
            print(f"  [extensions] failed to load command {f.name}: {exc}", file=sys.stderr)
    return loaded


def load_command_extensions(
    app: Any, *, include_entry_points: bool = True, include_local: bool = True
) -> dict[str, str]:
    """Register CLI command extensions on a Typer app.

    Command extensions expose a ``register(app)`` callable, either as a
    ``scrape_gateway.commands`` package entry point or as a local Python file in
    ``~/.config/scrape-gateway/commands``.
    """
    loaded: dict[str, str] = {}
    if include_entry_points:
        loaded.update(_entrypoint_command_extensions(app))
    if include_local:
        loaded.update(_local_command_extensions(app))
    return loaded
