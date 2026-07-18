"""Contract tests for the provider interface and extension discovery.

Ensures the ProviderAdapter API stays stable so extensions don't break,
and that discovery finds providers from all three sources.
"""

import importlib.util
import inspect
import sys
from pathlib import Path


from scrape_gateway.discovery import (
    _entrypoint_providers,
    _local_providers,
    discover_providers,
    discover_providers_with_sources,
)

SHIPPED_PROVIDERS = frozenset(
    {
        "raw_http",
        "wreq",
        "curl_cffi",
        "scrapedrive",
        "scrape_do",
        "scrapingbee",
        "scraperapi",
        "scrapfly",
        "firecrawl",
        "jina_reader",
        "zenrows",
        "oxylabs",
        "brightdata",
        "spider_cloud",
    }
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
        for name in SHIPPED_PROVIDERS:
            assert name in providers, f"built-in provider {name!r} not found via entry points"

    def test_builtin_names_match_class_names(self):
        providers = _entrypoint_providers()
        for name in SHIPPED_PROVIDERS:
            cls = providers[name]
            assert cls.name == name

    def test_all_builtins_subclass_provider_adapter(self):
        providers = _entrypoint_providers()
        for name in SHIPPED_PROVIDERS:
            cls = providers[name]
            assert issubclass(cls, ProviderAdapter)

    def test_all_builtins_have_cost_rank(self):
        providers = _entrypoint_providers()
        for name in SHIPPED_PROVIDERS:
            cls = providers[name]
            assert isinstance(cls.cost_rank, int)

    def test_all_builtins_have_capabilities(self):
        providers = _entrypoint_providers()
        for name in SHIPPED_PROVIDERS:
            cls = providers[name]
            assert isinstance(cls.capabilities, frozenset)
            assert "html" in cls.capabilities

    def test_all_builtins_have_async_scrape(self):
        providers = _entrypoint_providers()
        for name in SHIPPED_PROVIDERS:
            cls = providers[name]
            assert inspect.iscoroutinefunction(cls.scrape)

    def test_free_providers_have_low_cost(self):
        providers = _entrypoint_providers()
        for name in ("raw_http", "wreq", "curl_cffi"):
            assert providers[name].cost_rank < 10

    def test_paid_providers_have_higher_cost(self):
        providers = _entrypoint_providers()
        for name in (
            "scrapedrive",
            "scrape_do",
            "scrapingbee",
            "scraperapi",
            "scrapfly",
            "firecrawl",
            "zenrows",
            "oxylabs",
            "brightdata",
            "spider_cloud",
        ):
            assert providers[name].cost_rank >= 20

    def test_readme_capability_matrix_matches_every_current_provider(self):
        providers = {
            name: provider
            for name, provider in _entrypoint_providers().items()
            if name in SHIPPED_PROVIDERS
        }
        browserless_path = (
            Path(__file__).resolve().parents[1]
            / "extensions/sg-browserless/src/sg_browserless/__init__.py"
        )
        spec = importlib.util.spec_from_file_location(
            "sg_browserless_readme_test", browserless_path
        )
        assert spec and spec.loader
        browserless_module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = browserless_module
        spec.loader.exec_module(browserless_module)
        providers["browserless"] = browserless_module.BrowserlessProvider

        header = "| Provider | JS | Screenshot | Markdown | Country | CAPTCHA | Cost tier |"
        readme_lines = (Path(__file__).resolve().parents[1] / "README.md").read_text().splitlines()
        assert header in readme_lines, "README provider capability matrix is missing"
        header_index = readme_lines.index(header)
        rows = {}
        for line in readme_lines[header_index + 2 :]:
            if not line.startswith("|"):
                break
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            rows[cells[0].strip("`")] = dict(zip(header.strip("|").split("|"), cells))

        assert set(rows) == set(providers)
        capability_columns = {
            " JS ": "render_js",
            " Screenshot ": "screenshot",
            " Markdown ": "markdown",
            " Country ": "country",
        }
        for name, provider in providers.items():
            for column, capability in capability_columns.items():
                documented = rows[name][column].lower().startswith("yes")
                assert documented is (capability in provider.capabilities), (
                    f"README {column.strip()} capability is stale for {name}"
                )
            assert f"rank {provider.cost_rank}" in rows[name][" Cost tier "].lower()


# -- Discovery tests ----------------------------------------------------------


class TestDiscovery:
    def test_discover_finds_all_builtins(self):
        providers = discover_providers()
        for name in SHIPPED_PROVIDERS:
            assert name in providers

    def test_discover_with_sources_labels_shipped(self):
        result = discover_providers_with_sources()
        for name in SHIPPED_PROVIDERS:
            assert name in result
            _, source = result[name]
            assert source == "package"

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
