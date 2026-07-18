from __future__ import annotations

import base64
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from scrape_gateway import ProviderAdapter, ScrapeRequest


ROOT = Path(__file__).resolve().parents[1]
TARGET = "https://example.com/products"
HTML = "<html><body><h1>Products</h1><p>Local browser result with useful content.</p></body></html>"


def load_extension(package: str):
    path = ROOT / "extensions" / package / "src" / package.replace("-", "_") / "__init__.py"
    name = f"{package.replace('-', '_')}_contract_test"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("package", "class_name", "provider_name"),
    [
        ("sg-scrapling", "ScraplingProvider", "scrapling"),
        ("sg-spider-rs", "SpiderRsProvider", "spider_rs"),
        ("sg-camoufox", "CamoufoxProvider", "camoufox"),
        ("sg-seleniumbase", "SeleniumBaseProvider", "seleniumbase"),
        ("sg-patchright", "PatchrightProvider", "patchright"),
        ("sg-nodriver", "NodriverProvider", "nodriver"),
        ("sg-crawlee", "CrawleeProvider", "crawlee"),
    ],
)
def test_local_engine_extensions_publish_provider_contract(
    package: str, class_name: str, provider_name: str
) -> None:
    provider_class = getattr(load_extension(package), class_name)

    assert issubclass(provider_class, ProviderAdapter)
    assert provider_class.name == provider_name
    assert "html" in provider_class.capabilities


async def test_scrapling_uses_async_static_fetcher(monkeypatch) -> None:
    response = types.SimpleNamespace(
        status=200,
        body=HTML.encode(),
        encoding="utf-8",
        url=TARGET,
    )

    class AsyncFetcher:
        @staticmethod
        async def get(url, **kwargs):
            assert url == TARGET
            assert kwargs["headers"] == {"X-Test": "yes"}
            return response

    fetchers = types.ModuleType("scrapling.fetchers")
    fetchers.AsyncFetcher = AsyncFetcher
    fetchers.StealthyFetcher = object
    monkeypatch.setitem(sys.modules, "scrapling", types.ModuleType("scrapling"))
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", fetchers)
    provider = load_extension("sg-scrapling").ScraplingProvider()

    result = await provider.scrape(ScrapeRequest(TARGET, headers={"X-Test": "yes"}))

    assert result.success is True
    assert result.html == HTML
    assert result.route == "scrapling:http"


async def test_spider_rs_fetches_one_page_off_thread(monkeypatch) -> None:
    class Page:
        def __init__(self, url):
            assert url == TARGET

        def fetch(self):
            return None

        def get_html(self):
            return HTML

    spider = types.ModuleType("spider_rs")
    spider.Page = Page
    monkeypatch.setitem(sys.modules, "spider_rs", spider)
    provider = load_extension("sg-spider-rs").SpiderRsProvider()

    result = await provider.scrape(ScrapeRequest(TARGET))

    assert result.success is True
    assert result.html == HTML
    assert result.route == "spider_rs:page"


class FakePage:
    async def goto(self, url, **kwargs):
        assert url == TARGET

    async def content(self):
        return HTML

    async def screenshot(self, **kwargs):
        return b"browser-png"

    async def wait_for_selector(self, selector, **kwargs):
        assert selector == "#products"

    async def wait_for_timeout(self, milliseconds):
        assert milliseconds == 250


class FakeBrowser:
    async def new_page(self, **kwargs):
        return FakePage()

    async def close(self):
        return None


async def test_camoufox_returns_rendered_html_and_screenshot(monkeypatch) -> None:
    class AsyncCamoufox:
        def __init__(self, **kwargs):
            assert kwargs["headless"] is True

        async def __aenter__(self):
            return FakeBrowser()

        async def __aexit__(self, *args):
            return None

    api = types.ModuleType("camoufox.async_api")
    api.AsyncCamoufox = AsyncCamoufox
    monkeypatch.setitem(sys.modules, "camoufox", types.ModuleType("camoufox"))
    monkeypatch.setitem(sys.modules, "camoufox.async_api", api)
    provider = load_extension("sg-camoufox").CamoufoxProvider()

    result = await provider.scrape(
        ScrapeRequest(TARGET, screenshot=True, wait_selector="#products", extra_wait_ms=250)
    )

    assert result.success is True
    assert result.html == HTML
    assert result.screenshot == b"browser-png"


async def test_seleniumbase_uses_async_cdp_mode(monkeypatch) -> None:
    class Page:
        async def get_content(self):
            return HTML

        async def save_screenshot(self, filename, **kwargs):
            Path(filename).write_bytes(b"selenium-png")

    class Browser:
        async def get(self, url):
            assert url == TARGET
            return Page()

        def stop(self):
            return None

    async def start_async(**kwargs):
        assert kwargs["headless"] is True
        return Browser()

    seleniumbase = types.ModuleType("seleniumbase")
    undetected = types.ModuleType("seleniumbase.undetected")
    cdp_driver = types.ModuleType("seleniumbase.undetected.cdp_driver")
    cdp_driver.start_async = start_async
    monkeypatch.setitem(sys.modules, "seleniumbase", seleniumbase)
    monkeypatch.setitem(sys.modules, "seleniumbase.undetected", undetected)
    monkeypatch.setitem(sys.modules, "seleniumbase.undetected.cdp_driver", cdp_driver)
    provider = load_extension("sg-seleniumbase").SeleniumBaseProvider()

    result = await provider.scrape(ScrapeRequest(TARGET, screenshot=True))

    assert result.success is True
    assert result.html == HTML
    assert result.screenshot == b"selenium-png"
    assert result.route == "seleniumbase:cdp"


class FakePlaywright:
    def __init__(self):
        self.chromium = self

    async def launch(self, **kwargs):
        return FakeBrowser()


async def test_patchright_uses_async_playwright_contract(monkeypatch) -> None:
    class Manager:
        async def __aenter__(self):
            return FakePlaywright()

        async def __aexit__(self, *args):
            return None

    api = types.ModuleType("patchright.async_api")
    api.async_playwright = Manager
    monkeypatch.setitem(sys.modules, "patchright", types.ModuleType("patchright"))
    monkeypatch.setitem(sys.modules, "patchright.async_api", api)
    provider = load_extension("sg-patchright").PatchrightProvider()

    result = await provider.scrape(ScrapeRequest(TARGET, screenshot=True))

    assert result.success is True
    assert result.html == HTML
    assert result.screenshot == b"browser-png"


async def test_nodriver_uses_base64_screenshot_without_temp_files(monkeypatch) -> None:
    class Page:
        async def get_content(self):
            return HTML

        async def save_screenshot(self, **kwargs):
            assert kwargs["as_base64"] is True
            return base64.b64encode(b"nodriver-png").decode()

    class Browser:
        async def get(self, url):
            assert url == TARGET
            return Page()

        def stop(self):
            return None

    async def start(**kwargs):
        return Browser()

    module = types.ModuleType("nodriver")
    module.start = start
    monkeypatch.setitem(sys.modules, "nodriver", module)
    provider = load_extension("sg-nodriver").NodriverProvider()

    result = await provider.scrape(ScrapeRequest(TARGET, screenshot=True))

    assert result.success is True
    assert result.html == HTML
    assert result.screenshot == b"nodriver-png"


async def test_crawlee_captures_one_playwright_request(monkeypatch) -> None:
    class Router:
        def default_handler(self, function):
            self.handler = function
            return function

    class PlaywrightCrawler:
        def __init__(self, **kwargs):
            assert kwargs["max_requests_per_crawl"] == 1
            self.router = Router()

        async def run(self, urls):
            assert urls == [TARGET]
            await self.router.handler(types.SimpleNamespace(page=FakePage()))

    module = types.ModuleType("crawlee.crawlers")
    module.PlaywrightCrawler = PlaywrightCrawler
    monkeypatch.setitem(sys.modules, "crawlee", types.ModuleType("crawlee"))
    monkeypatch.setitem(sys.modules, "crawlee.crawlers", module)
    provider = load_extension("sg-crawlee").CrawleeProvider()

    result = await provider.scrape(ScrapeRequest(TARGET, screenshot=True))

    assert result.success is True
    assert result.html == HTML
    assert result.screenshot == b"browser-png"
