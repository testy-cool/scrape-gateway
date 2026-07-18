# Providers

15 built-in, 5 usable without a paid credential. The router tries lower cost ranks first,
then escalates to paid services.

| Provider | Cost Rank | Free | JS | Geo | Anti-bot | Notes |
|---|---|---|---|---|---|---|
| `raw_http` | 0 | yes | no | no | none | Plain HTTP GET via httpx |
| `wreq` | 2 | yes | no | no | TLS fingerprinting | Impersonates real browser TLS |
| `curl_cffi` | 3 | yes | no | no | TLS fingerprinting | Similar to wreq, different engine |
| `jina_reader` | 8 | yes | yes | no | managed browser | Markdown-first; API key is optional |
| `crawl4ai` | 18 | self-hosted | yes | no | browser | Docker API; HTML, Markdown, screenshots |
| `spider_cloud` | 24 | no | yes | no | smart mode | Hosted Spider single-page API |
| `scrapedrive` | 25 | no | yes | yes | full | 3 tiers: standard / advanced / hyperdrive |
| `firecrawl` | 26 | no | yes | yes | stealth proxy | Native HTML, Markdown, and screenshots |
| `scrape_do` | 30 | no | yes | yes | residential proxies | |
| `scrapfly` | 32 | no | yes | yes | ASP | Per-call cost budget |
| `zenrows` | 34 | no | yes | yes | premium proxies | Manual Universal Scraper API options |
| `scrapingbee` | 35 | no | yes | yes | premium proxies | |
| `scraperapi` | 40 | no | yes | yes | premium proxies | Supports screenshots |
| `oxylabs` | 45 | no | yes | yes | automatic | Realtime Universal source; PNG screenshots |
| `brightdata` | 50 | no | yes | no | Web Unlocker | Raw HTML or PNG screenshots |

## API parameter mapping

Each provider has its own API conventions. The adapter layer translates `sgw`'s common interface.

### ScrapeDrive

- Endpoint: configurable via `SCRAPEDRIVE_BASE_URL`
- Auth: `SCRAPEDRIVE_API_KEY`
- Tiers: standard → advanced → hyperdrive (auto-escalates on failure)
- `premium` flag maps to hyperdrive tier
- `country` auto-upgrades to advanced tier (geo-proxies required)

### Scrape.do

- Endpoint: `https://api.scrape.do/`
- Auth: `SCRAPE_DO_TOKEN` (passed as `token`)
- `country` → `geoCode`
- `premium` → `super=true`
- `render_js` → `render=true`

### ScrapingBee

- Endpoint: `https://app.scrapingbee.com/api/v1/`
- Auth: `SCRAPINGBEE_API_KEY` (passed as `api_key`)
- `render_js` → `render_js`
- `premium` → `premium_proxy=true`
- `country` → `country_code`

### ScraperAPI

- Endpoint: `https://api.scraperapi.com/`
- Auth: `SCRAPERAPI_API_KEY` (passed as `api_key`)
- `render_js` → `render=true`
- `premium` → `premium=true`
- `country` → `country_code`
- `screenshot` → `screenshot=true`

### Jina Reader

- Endpoint: `https://r.jina.ai/<target-url>`
- Auth: optional `JINA_API_KEY` bearer token
- Returns Markdown natively; the gateway also uses that text for deterministic validation
- `render_js` → `X-Engine: browser`
- `wait_selector` → `X-Wait-For-Selector`
- Official contract: [Jina Reader](https://github.com/jina-ai/reader)

### Spider Cloud

- Endpoint: `POST https://api.spider.cloud/scrape`
- Auth: `SPIDER_CLOUD_API_KEY` bearer token
- `render_js` → `request: chrome`; `premium` → `request: smart_mode`
- Markdown output uses `return_format: markdown`; other requests use raw HTML
- Official contract: [Spider API overview](https://spider.cloud/llms.txt)

### Firecrawl

- Endpoint: `POST https://api.firecrawl.dev/v2/scrape` (override with `FIRECRAWL_BASE_URL`)
- Auth: `FIRECRAWL_API_KEY` bearer token
- Requests HTML and Markdown together; screenshots use the v2 screenshot format object
- Country, mobile viewport, wait time, ad blocking, headers, and provider timeout map to v2 fields
- Official contract: [Firecrawl v2 scrape](https://docs.firecrawl.dev/api-reference/endpoint/scrape)

### Scrapfly

- Endpoint: `GET https://api.scrapfly.io/scrape`
- Auth: `SCRAPFLY_API_KEY` as the documented `key` query parameter
- `render_js`, `country`, `wait_for_selector`, rendering delay, and optional session are mapped
- `premium` enables ASP with a configurable per-call `cost_budget` (default 25 credits)
- Reported API credits become `cost_units`; CLOB responses are downloaded transparently
- Official contract: [Scrapfly Scrape API](https://scrapfly.io/docs/scrape-api/getting-started)

### Crawl4AI

- Endpoint: `POST {CRAWL4AI_URL}/crawl`
- Auth: optional `CRAWL4AI_TOKEN` bearer token; Crawl4AI 0.9 enables auth by default
- Sends typed `BrowserConfig` and `CrawlerRunConfig` objects accepted by the Docker API
- Gateway cache bypasses Crawl4AI's internal cache to avoid two independent freshness layers
- Wait selectors map to `wait_for=css:<selector>`; viewport, headers, timeout, delay, and screenshot map to their native config fields
- Decodes native Markdown objects and base64 screenshots from the crawl result
- Official contract: [Crawl4AI self-hosting](https://docs.crawl4ai.com/core/self-hosting/)

### ZenRows

- Endpoint: `GET https://api.zenrows.com/v1/`
- Auth: `ZENROWS_API_KEY` as `apikey`
- `render_js` → `js_render`; `premium` → `premium_proxy`
- Country requests enable premium proxy and set `proxy_country`
- Official contract: [ZenRows Universal Scraper API](https://docs.zenrows.com/universal-scraper-api/api-reference)

### Oxylabs

- Endpoint: `POST https://realtime.oxylabs.io/v1/queries`
- Auth: HTTP Basic with `OXYLABS_USERNAME` and `OXYLABS_PASSWORD`
- Uses the `universal` source; country and mobile map to `geo_location` and `user_agent_type`
- JavaScript uses `render: html`; screenshot requests use `render: png` and decode base64 output
- Official contract: [Oxylabs Realtime API](https://developers.oxylabs.io/scraping-solutions/web-scraper-api/integration-methods/realtime)

### Bright Data Web Unlocker

- Endpoint: `POST https://api.brightdata.com/request`
- Auth: `BRIGHTDATA_API_KEY` bearer token plus `BRIGHTDATA_WEB_UNLOCKER_ZONE`
- Raw requests return HTML; screenshot requests use `data_format: screenshot` and return PNG
- Official contract: [Bright Data Web Unlocker](https://docs.brightdata.com/scraping-automation/web-unlocker/send-your-first-request)

## Extension providers

### Browserless (`extensions/sg-browserless`)

- Install: `uv pip install -e extensions/sg-browserless`
- Auth: `BROWSERLESS_URL` and `BROWSERLESS_TOKEN`
- Endpoints: `{BROWSERLESS_URL}/content` and `{BROWSERLESS_URL}/screenshot`
- Request auth: `Authorization: Bearer <BROWSERLESS_TOKEN>` keeps credentials out of request URLs and logs
- `wait_event=networkidle` maps to Browserless/Puppeteer `networkidle2`

### Local engine extensions

Each engine is isolated in its own package so installing one does not pull every browser
runtime into the gateway environment.

| Extension | Provider | Runtime | Capabilities |
|---|---|---|---|
| `extensions/sg-requests` | `requests` | Python Requests worker thread | HTML |
| `extensions/sg-botasaurus` | `botosaurus` | Botasaurus Request | HTML, fingerprinted HTTP |
| `extensions/sg-playwright` | `playwright` | direct Playwright Chromium | HTML, JS, screenshot |
| `extensions/sg-pydoll` | `pydoll` | direct Chrome DevTools | HTML, JS, screenshot |
| `extensions/sg-helium` | `helium` | Helium / Selenium Chrome | HTML, JS, screenshot |
| `extensions/sg-scrapy` | `scrapy` | isolated Twisted crawler worker | HTML |
| `extensions/sg-cdp` | `chrome_cdp`, `lightpanda` | external CDP browser | HTML, JS; Chrome screenshots |
| `extensions/sg-scrapling` | `scrapling` | Scrapling HTTP / stealth Patchright | HTML, JS/stealth |
| `extensions/sg-spider-rs` | `spider_rs` | Rust-backed Page API | single-page HTML |
| `extensions/sg-camoufox` | `camoufox` | fingerprinted Firefox | HTML, JS, screenshot |
| `extensions/sg-seleniumbase` | `seleniumbase` | SeleniumBase async CDP Mode | HTML, JS, screenshot |
| `extensions/sg-patchright` | `patchright` | patched Chromium | HTML, JS, screenshot |
| `extensions/sg-nodriver` | `nodriver` | direct Chrome DevTools | HTML, JS, screenshot |
| `extensions/sg-crawlee` | `crawlee` | bounded PlaywrightCrawler | HTML, JS, screenshot |

Install instructions and required browser bootstrap commands live in each extension's README.
The adapter contracts follow the official [Playwright Python API](https://playwright.dev/python/docs/api/class-playwright), [Playwright CDP API](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp), [Pydoll API](https://pydoll.tech/), [Scrapy API](https://docs.scrapy.org/en/latest/), [Scrapling fetcher guide](https://scrapling.readthedocs.io/en/latest/fetching/choosing.html), [spider-rs Python guide](https://github.com/spider-rs/spider-py), [Camoufox Python API](https://camoufox.com/python/usage/), [SeleniumBase CDP Mode](https://seleniumbase.io/examples/cdp_mode/ReadMe/), [Patchright Python API](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python), [Nodriver API](https://ultrafunkamsterdam.github.io/nodriver/nodriver/quickstart.html), and [Crawlee PlaywrightCrawler API](https://crawlee.dev/python/api/class/PlaywrightCrawler).

`sg-spider-rs` is staged rather than marked available: upstream PyPI 0.0.57 has invalid
metadata and the v0.0.58 Git source currently fails to compile against the `spider` crate
version it resolves on Linux. The adapter and contract tests remain in-tree so it can be
enabled as soon as upstream ships a buildable dependency.
