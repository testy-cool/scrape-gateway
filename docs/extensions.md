# Writing extensions

The 15 built-in providers cover most scraping needs, but you can add your own — an Amazon Product API, a Wayback Machine fetcher, a headless browser, anything that takes a URL and returns content.

You can also add custom CLI commands, such as a sitemap discovery command backed by Trafilatura.

## Provider extensions

Three ways to add providers, in order of effort:

## 1. Drop a file (easiest)

Put a `.py` file in `~/.config/scrape-gateway/providers/`:

```python
from scrape_gateway import ProviderAdapter, ScrapeRequest, ScrapeResult

class MyProvider(ProviderAdapter):
    name = "my_api"
    cost_rank = 10
    capabilities = frozenset({"html"})
    install_requires = ["some-package"]  # auto-installed on first use

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        # your logic here
        ...
```

`sgw` discovers it automatically. Run `sgw providers` to verify.

If your provider needs a pip package, set `install_requires` — `sgw` will prompt to install it the first time it loads.

## 2. Install from the registry

```bash
sgw extensions                    # browse available extensions
sgw extensions sg-camoufox        # install one into sgw's own venv
```

## 3. Publish a pip package

Create a package that declares an entry point:

```toml
[project.entry-points."scrape_gateway.providers"]
my_provider = "my_package:MyProvider"
```

After `pip install my-package`, `sgw` discovers it automatically.

See `examples/extension_example.py` for a complete template.

## Command extensions

Command extensions register additional `sgw` subcommands. They expose a callable named `register(app)`, either from a package entry point or a local Python file.

### Local command file

Put a `.py` file in `~/.config/scrape-gateway/commands/`:

```python
import typer


def register(app: typer.Typer) -> None:
    @app.command("hello")
    def hello(name: str = "world") -> None:
        print(f"hello {name}")
```

Run it:

```bash
sgw hello Vlad
```

### Package command extension

Create a package that declares an entry point:

```toml
[project.entry-points."scrape_gateway.commands"]
hello = "my_package:register"
```

After `pip install my-package`, `sgw` loads the command automatically.

### Trafilatura sitemap extension

This repo includes an example command extension at `extensions/sg-sitemap`:

```bash
uv pip install -e . -e extensions/sg-sitemap
sgw sitemap https://example.com
sgw sitemap https://example.com -f txt --limit 50
sgw sitemap https://example.com --discover-only
```

`sgw sitemap` uses Trafilatura's `sitemap_search()` to expand sitemap files into page URLs. `--discover-only` reports sitemap URLs advertised in `robots.txt` without expanding them.

### Cache inspection extension

This repo includes a cache command extension at `extensions/sg-cache`:

```bash
uv pip install -e . -e extensions/sg-cache
sgw cache stats
sgw cache ls --domain example.com
sgw cache show https://example.com
sgw cache purge --expired --yes
```

`sgw cache` reads the configured artifact root, defaults to `.scrape-gateway/artifacts`, and is useful for long-lived MCP deployments where cache state persists across container redeploys.

### Browserless provider extension

This repo includes a Browserless provider extension at `extensions/sg-browserless`:

```bash
uv pip install -e . -e extensions/sg-browserless
```

Set credentials in `.env`:

```bash
BROWSERLESS_URL=https://browserless.example.com
BROWSERLESS_TOKEN=your_token_here
```

Use it for rendered HTML or screenshots:

```bash
sgw providers
sgw url https://example.com --render-js -p browserless
sgw url https://example.com --render-js --screenshot -p browserless
```

With `--screenshot`, the extension fetches `/content` and `/screenshot` concurrently
so the result contains both rendered HTML and visual evidence for validation or AI audit.

### ScrapingEvals local engine providers

Thirteen available source extensions turn the deterministic engines from the Zenbook
ScrapingEvals lab into optional Gateway providers while keeping heavyweight dependencies
out of the default installation:

```bash
uv pip install -e extensions/sg-requests
uv pip install -e extensions/sg-botasaurus
uv pip install -e extensions/sg-playwright
uv pip install -e extensions/sg-pydoll
uv pip install -e extensions/sg-helium
uv pip install -e extensions/sg-scrapy
uv pip install -e extensions/sg-cdp
uv pip install -e extensions/sg-scrapling
uv pip install -e extensions/sg-camoufox
uv pip install -e extensions/sg-seleniumbase
uv pip install -e extensions/sg-patchright
uv pip install -e extensions/sg-nodriver
uv pip install -e extensions/sg-crawlee
```

Use only the package or packages needed by the deployment. Playwright, Scrapling,
Camoufox, Patchright, and Crawlee require the browser bootstrap command documented in
their package README after installation. Pydoll and Helium use an installed Chrome or
Chromium. `sg-cdp` attaches to `CHROME_CDP_URL` or `LIGHTPANDA_CDP_URL` instead of
starting a browser. Scrapy runs each crawl in a child process so Twisted's reactor never
pollutes the long-running Gateway process.

The ScrapingEvals Requests and HTTPX baselines map to `requests` and the built-in
`raw_http` provider respectively. Its separate Camoufox headed/tuned tracks are runtime
configurations of the `camoufox` engine, not distinct provider contracts.

Agentic tools such as browser-use, Stagehand, and Skyvern are not registered as URL
providers: their success includes an LLM task and action trace, which does not fit the
deterministic `scrape(url) -> content` contract. Puppeteer still needs a safe Node
dependency/bootstrap contract. `sg-spider-rs` is staged in-tree, but its README documents
the upstream Linux build failure that keeps it out of the available registry until the
dependency is installable.
