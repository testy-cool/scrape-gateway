from __future__ import annotations

import base64
import asyncio
import importlib.util
import io
import json
import sys
import tomllib
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
        ("sg-requests", "RequestsProvider", "requests"),
        ("sg-botasaurus", "BotasaurusProvider", "botosaurus"),
        ("sg-playwright", "PlaywrightProvider", "playwright"),
        ("sg-pydoll", "PydollProvider", "pydoll"),
        ("sg-helium", "HeliumProvider", "helium"),
        ("sg-scrapy", "ScrapyProvider", "scrapy"),
        ("sg-cdp", "ChromeCdpProvider", "chrome_cdp"),
        ("sg-cdp", "LightpandaProvider", "lightpanda"),
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


async def test_requests_runs_blocking_fetch_off_thread(monkeypatch) -> None:
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return types.SimpleNamespace(
            status_code=200,
            text=HTML,
            url=TARGET,
        )

    module = types.ModuleType("requests")
    module.get = get
    monkeypatch.setitem(sys.modules, "requests", module)
    provider = load_extension("sg-requests").RequestsProvider()

    result = await provider.scrape(ScrapeRequest(TARGET, headers={"X-Test": "yes"}))

    assert result.success is True
    assert result.html == HTML
    assert result.route == "requests:http"
    assert calls == [
        (
            TARGET,
            {
                "headers": {"X-Test": "yes"},
                "timeout": 45,
                "allow_redirects": True,
            },
        )
    ]


async def test_botasaurus_uses_request_client(monkeypatch) -> None:
    class Request:
        def get(self, url, **kwargs):
            assert url == TARGET
            assert kwargs["headers"] == {"X-Test": "yes"}
            return types.SimpleNamespace(status_code=200, text=HTML, url=TARGET)

    root = types.ModuleType("botasaurus")
    request_module = types.ModuleType("botasaurus.request")
    request_module.Request = Request
    monkeypatch.setitem(sys.modules, "botasaurus", root)
    monkeypatch.setitem(sys.modules, "botasaurus.request", request_module)
    provider = load_extension("sg-botasaurus").BotasaurusProvider()

    result = await provider.scrape(ScrapeRequest(TARGET, headers={"X-Test": "yes"}))

    assert result.success is True
    assert result.html == HTML
    assert result.route == "botosaurus:request"


async def test_playwright_returns_response_status_html_and_screenshot(monkeypatch) -> None:
    class Page(FakePage):
        url = TARGET

        async def goto(self, url, **kwargs):
            await super().goto(url, **kwargs)
            return types.SimpleNamespace(status=201)

        async def close(self):
            return None

    class Browser(FakeBrowser):
        async def new_page(self, **kwargs):
            assert kwargs["extra_http_headers"] == {"X-Test": "yes"}
            return Page()

    class Playwright:
        chromium = types.SimpleNamespace(launch=lambda **kwargs: None)

    async def launch(**kwargs):
        assert kwargs["headless"] is True
        return Browser()

    Playwright.chromium.launch = launch

    class Manager:
        async def __aenter__(self):
            return Playwright()

        async def __aexit__(self, *args):
            return None

    api = types.ModuleType("playwright.async_api")
    api.async_playwright = Manager
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", api)
    provider = load_extension("sg-playwright").PlaywrightProvider()

    result = await provider.scrape(
        ScrapeRequest(TARGET, headers={"X-Test": "yes"}, screenshot=True)
    )

    assert result.success is True
    assert result.status_code == 201
    assert result.html == HTML
    assert result.screenshot == b"browser-png"
    assert result.route == "playwright:chromium"


async def test_pydoll_uses_cdp_tab_and_base64_screenshot(monkeypatch) -> None:
    class ChromiumOptions:
        def __init__(self):
            self.binary_location = None
            self.headless = False
            self.arguments = []

        def add_argument(self, value):
            self.arguments.append(value)

    class Tab:
        page_source = HTML
        current_url = TARGET

        async def go_to(self, url, timeout):
            assert url == TARGET
            assert timeout == 45

        async def query(self, selector, timeout):
            assert selector == "#products"

        async def take_screenshot(self, **kwargs):
            assert kwargs["as_base64"] is True
            return base64.b64encode(b"pydoll-png").decode()

    class Chrome:
        def __init__(self, options):
            assert isinstance(options, ChromiumOptions)

        async def start(self, headless):
            assert headless is True

        async def new_tab(self):
            return Tab()

        async def stop(self):
            return None

    chromium = types.ModuleType("pydoll.browser.chromium")
    chromium.Chrome = Chrome
    options = types.ModuleType("pydoll.browser.options")
    options.ChromiumOptions = ChromiumOptions
    monkeypatch.setitem(sys.modules, "pydoll", types.ModuleType("pydoll"))
    monkeypatch.setitem(sys.modules, "pydoll.browser", types.ModuleType("pydoll.browser"))
    monkeypatch.setitem(sys.modules, "pydoll.browser.chromium", chromium)
    monkeypatch.setitem(sys.modules, "pydoll.browser.options", options)
    provider = load_extension("sg-pydoll").PydollProvider()

    result = await provider.scrape(
        ScrapeRequest(TARGET, screenshot=True, wait_selector="#products")
    )

    assert result.success is True
    assert result.html == HTML
    assert result.screenshot == b"pydoll-png"
    assert result.route == "pydoll:cdp"


async def test_helium_runs_selenium_session_off_thread(monkeypatch) -> None:
    class Driver:
        page_source = HTML
        current_url = TARGET

        def get_screenshot_as_png(self):
            return b"helium-png"

    driver = Driver()
    stopped = []

    helium = types.ModuleType("helium")
    helium.start_chrome = lambda *args, **kwargs: None
    helium.get_driver = lambda: driver
    helium.kill_browser = lambda: stopped.append(True)

    class Options:
        def __init__(self):
            self.arguments = []
            self.binary_location = None

        def add_argument(self, value):
            self.arguments.append(value)

        def add_experimental_option(self, name, value):
            return None

    selenium = types.ModuleType("selenium")
    webdriver = types.ModuleType("selenium.webdriver")
    chrome = types.ModuleType("selenium.webdriver.chrome")
    options = types.ModuleType("selenium.webdriver.chrome.options")
    options.Options = Options
    monkeypatch.setitem(sys.modules, "helium", helium)
    monkeypatch.setitem(sys.modules, "selenium", selenium)
    monkeypatch.setitem(sys.modules, "selenium.webdriver", webdriver)
    monkeypatch.setitem(sys.modules, "selenium.webdriver.chrome", chrome)
    monkeypatch.setitem(sys.modules, "selenium.webdriver.chrome.options", options)
    provider = load_extension("sg-helium").HeliumProvider()

    result = await provider.scrape(ScrapeRequest(TARGET, screenshot=True))

    assert result.success is True
    assert result.html == HTML
    assert result.screenshot == b"helium-png"
    assert result.route == "helium:selenium"
    assert stopped == [True]


async def test_scrapy_uses_isolated_worker_process(monkeypatch) -> None:
    payload = {
        "status_code": 200,
        "html": HTML,
        "final_url": TARGET,
    }

    class Process:
        returncode = 0

        async def communicate(self, input):
            request = json.loads(input)
            assert request["url"] == TARGET
            assert request["headers"] == {"X-Test": "yes"}
            return json.dumps(payload).encode(), b""

        def kill(self):
            return None

        async def wait(self):
            return None

    async def create_subprocess_exec(*args, **kwargs):
        assert args[-1] == "sg_scrapy._worker"
        assert kwargs["stdin"] is asyncio.subprocess.PIPE
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)
    provider = load_extension("sg-scrapy").ScrapyProvider()

    result = await provider.scrape(ScrapeRequest(TARGET, headers={"X-Test": "yes"}))

    assert result.success is True
    assert result.html == HTML
    assert result.metadata["final_url"] == TARGET
    assert result.route == "scrapy:spider"


def test_scrapy_worker_uses_modern_async_start(monkeypatch, capsys) -> None:
    captured = {}

    class Spider:
        pass

    class Request:
        def __init__(self, url, **kwargs):
            self.url = url
            self.kwargs = kwargs

    class CrawlerProcess:
        def __init__(self, **kwargs):
            return None

        def crawl(self, spider_class):
            captured["spider_class"] = spider_class

        def start(self, **kwargs):
            async def drive_spider():
                spider = captured["spider_class"]()
                requests = [item async for item in spider.start()]
                assert requests[0].url == TARGET
                spider.parse(types.SimpleNamespace(status=200, text=HTML, url=TARGET))

            asyncio.run(drive_spider())

    scrapy = types.ModuleType("scrapy")
    scrapy.Spider = Spider
    scrapy.Request = Request
    crawler = types.ModuleType("scrapy.crawler")
    crawler.CrawlerProcess = CrawlerProcess
    monkeypatch.setitem(sys.modules, "scrapy", scrapy)
    monkeypatch.setitem(sys.modules, "scrapy.crawler", crawler)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "url": TARGET,
                    "headers": {},
                    "referer": None,
                    "timeout_seconds": 45,
                }
            )
        ),
    )

    path = ROOT / "extensions/sg-scrapy/src/sg_scrapy/_worker.py"
    spec = importlib.util.spec_from_file_location("sg_scrapy_worker_contract_test", path)
    assert spec and spec.loader
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    worker.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["status_code"] == 200
    assert payload["html"] == HTML


async def test_cdp_providers_attach_to_configured_endpoint(monkeypatch) -> None:
    class Page(FakePage):
        url = TARGET

        async def close(self):
            return None

    class Context:
        async def new_page(self):
            return Page()

    class Browser:
        contexts = [Context()]

        async def close(self):
            return None

    class Chromium:
        async def connect_over_cdp(self, endpoint, **kwargs):
            assert endpoint == "http://127.0.0.1:9222"
            return Browser()

    class Manager:
        async def __aenter__(self):
            return types.SimpleNamespace(chromium=Chromium())

        async def __aexit__(self, *args):
            return None

    api = types.ModuleType("playwright.async_api")
    api.async_playwright = Manager
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", api)
    monkeypatch.setenv("CHROME_CDP_URL", "http://127.0.0.1:9222")
    provider = load_extension("sg-cdp").ChromeCdpProvider()

    result = await provider.scrape(ScrapeRequest(TARGET, screenshot=True))

    assert result.success is True
    assert result.html == HTML
    assert result.screenshot == b"browser-png"
    assert result.route == "chrome_cdp:cdp"


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
    new_page_kwargs: dict[str, object] = {}

    class CamoufoxBrowser(FakeBrowser):
        async def new_page(self, **kwargs):
            new_page_kwargs.update(kwargs)
            return FakePage()

    class AsyncCamoufox:
        def __init__(self, **kwargs):
            assert kwargs["headless"] is True

        async def __aenter__(self):
            return CamoufoxBrowser()

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
    assert new_page_kwargs["no_viewport"] is True


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


def test_nodriver_excludes_broken_0_50_3_release() -> None:
    pyproject = tomllib.loads((ROOT / "extensions" / "sg-nodriver" / "pyproject.toml").read_text())

    assert "nodriver>=0.48,<0.50.3" in pyproject["project"]["dependencies"]


async def test_nodriver_reads_filename_based_screenshot(monkeypatch) -> None:
    class Page:
        async def get_content(self):
            return HTML

        async def save_screenshot(self, filename, **kwargs):
            assert kwargs == {"format": "png", "full_page": True}
            Path(filename).write_bytes(b"nodriver-png")
            return str(filename)

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
