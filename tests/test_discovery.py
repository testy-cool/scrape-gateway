"""Contract tests for the provider interface and extension discovery.

Ensures the ProviderAdapter API stays stable so extensions don't break,
and that discovery finds providers from all three sources.
"""

import importlib
import inspect
import tempfile
from pathlib import Path

import pytest

from scrape_gateway.discovery import (
    BUILTIN_NAMES,
    EXTENSIONS_DIR,
    _entrypoint_providers,
    _local_providers,
    discover_providers,
    discover_providers_with_sources,
)
from scrape_gateway.models import ScrapeRequest, ScrapeResult
from scrape_gateway.provider import ProviderAdapter


# -- Provider contract tests --------------------------------------------------


class TestProviderContract:
    """The ProviderAdapter interface that all extensions depend on."""

    def test_requires_name_attribute(self):
        assert "name" in ProviderAdapter.__annotations__

    def test_has_cost_rank_default(self):
        assert ProviderAdapter.cost_rank == 100

    def test_has_capabilities_default(self):
        assert ProviderAdapter.capabilities == frozenset({"html"})

    def test_has_install_requires_default(self):
        assert ProviderAdapter.install_requires == []

    def test_has_can_handle_method(self):
        assert hasattr(ProviderAdapter, "can_handle")
        sig = inspect.signature(ProviderAdapter.can_handle)
        params = list(sig.parameters)
        assert "request" in params

    def test_has_abstract_scrape_method(self):
        assert hasattr(ProviderAdapter, "scrape")
        assert inspect.iscoroutinefunction(ProviderAdapter.scrape)

    def test_scrape_request_has_url(self):
        req = ScrapeRequest(url="https://example.com")
        assert req.url == "https://example.com"

    def test_scrape_result_has_required_fields(self):
        result = ScrapeResult(url="https://example.com", provider="test", success=True)
        assert result.url == "https://example.com"
        assert result.provider == "test"
        assert result.success is True
        assert result.html is None
        assert result.markdown is None
        assert result.error is None


# -- Built-in provider contract tests -----------------------------------------


class TestBuiltinProviders:
    """Every built-in provider must conform to the ProviderAdapter contract."""

    def test_all_builtins_discovered(self):
        providers = _entrypoint_providers()
        for name in BUILTIN_NAMES:
            assert name in providers, f"built-in provider {name!r} not found via entry points"

    def test_builtin_names_match_class_names(self):
        providers = _entrypoint_providers()
        for name in BUILTIN_NAMES:
            cls = providers[name]
            assert cls.name == name

    def test_all_builtins_subclass_provider_adapter(self):
        providers = _entrypoint_providers()
        for name in BUILTIN_NAMES:
            cls = providers[name]
            assert issubclass(cls, ProviderAdapter)

    def test_all_builtins_have_cost_rank(self):
        providers = _entrypoint_providers()
        for name in BUILTIN_NAMES:
            cls = providers[name]
            assert isinstance(cls.cost_rank, int)

    def test_all_builtins_have_capabilities(self):
        providers = _entrypoint_providers()
        for name in BUILTIN_NAMES:
            cls = providers[name]
            assert isinstance(cls.capabilities, frozenset)
            assert "html" in cls.capabilities

    def test_all_builtins_have_async_scrape(self):
        providers = _entrypoint_providers()
        for name in BUILTIN_NAMES:
            cls = providers[name]
            assert inspect.iscoroutinefunction(cls.scrape)

    def test_free_providers_have_low_cost(self):
        providers = _entrypoint_providers()
        for name in ("raw_http", "wreq", "curl_cffi"):
            assert providers[name].cost_rank < 10

    def test_paid_providers_have_higher_cost(self):
        providers = _entrypoint_providers()
        for name in ("scrapedrive", "scrape_do", "scrapingbee", "scraperapi"):
            assert providers[name].cost_rank >= 25


# -- Discovery tests ----------------------------------------------------------


class TestDiscovery:
    def test_discover_finds_all_builtins(self):
        providers = discover_providers()
        for name in BUILTIN_NAMES:
            assert name in providers

    def test_discover_with_sources_labels_builtins(self):
        result = discover_providers_with_sources()
        for name in BUILTIN_NAMES:
            assert name in result
            _, source = result[name]
            assert source == "built-in"

    def test_local_provider_from_file(self, monkeypatch, tmp_path):
        ext_file = tmp_path / "fake_provider.py"
        ext_file.write_text(
            "from scrape_gateway.provider import ProviderAdapter\n"
            "from scrape_gateway.models import ScrapeRequest, ScrapeResult\n"
            "class FakeProvider(ProviderAdapter):\n"
            "    name = 'fake_test'\n"
            "    cost_rank = 999\n"
            "    async def scrape(self, request): ...\n"
        )
        monkeypatch.setattr("scrape_gateway.discovery.EXTENSIONS_DIR", tmp_path)
        result = _local_providers()
        assert "fake_test" in result
        assert result["fake_test"].name == "fake_test"
        assert result["fake_test"].cost_rank == 999

    def test_local_skips_underscore_files(self, monkeypatch, tmp_path):
        (tmp_path / "_internal.py").write_text(
            "from scrape_gateway.provider import ProviderAdapter\n"
            "class Hidden(ProviderAdapter):\n"
            "    name = 'hidden'\n"
            "    async def scrape(self, request): ...\n"
        )
        monkeypatch.setattr("scrape_gateway.discovery.EXTENSIONS_DIR", tmp_path)
        result = _local_providers()
        assert "hidden" not in result

    def test_local_handles_broken_files(self, monkeypatch, tmp_path, capsys):
        (tmp_path / "broken.py").write_text("this is not valid python !!!")
        monkeypatch.setattr("scrape_gateway.discovery.EXTENSIONS_DIR", tmp_path)
        result = _local_providers()
        assert result == {}
        captured = capsys.readouterr()
        assert "broken.py" in captured.err

    def test_local_overrides_entrypoint(self, monkeypatch, tmp_path):
        ext_file = tmp_path / "override.py"
        ext_file.write_text(
            "from scrape_gateway.provider import ProviderAdapter\n"
            "class Override(ProviderAdapter):\n"
            "    name = 'raw_http'\n"
            "    cost_rank = 777\n"
            "    async def scrape(self, request): ...\n"
        )
        monkeypatch.setattr("scrape_gateway.discovery.EXTENSIONS_DIR", tmp_path)
        result = discover_providers()
        assert result["raw_http"].cost_rank == 777

    def test_empty_extensions_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr("scrape_gateway.discovery.EXTENSIONS_DIR", tmp_path)
        result = _local_providers()
        assert result == {}

    def test_nonexistent_extensions_dir(self, monkeypatch):
        monkeypatch.setattr(
            "scrape_gateway.discovery.EXTENSIONS_DIR",
            Path("/nonexistent/path"),
        )
        result = _local_providers()
        assert result == {}
