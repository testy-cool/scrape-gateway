# scrape-gateway

One `scrape()` call. Many providers. Cached HTML/Markdown. Cost-aware fallback. Domain memory.

Use it when:

- `requests` works on some sites but fails randomly on others
- Playwright/browser scraping is too expensive to use by default
- you use multiple scraping providers and want one interface
- you want to remember which provider/country/tier worked per domain
- you want raw HTML and Markdown artifacts saved for LLM/agent pipelines

## Install locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

## Use from Python

```python
import asyncio
from scrape_gateway import ScrapeGateway, ScrapeRequest

async def main():
    gateway = ScrapeGateway()
    page = await gateway.scrape(ScrapeRequest(
        "https://example.com/product",
        country="us",
        render_js=False,
        premium=False,
    ))
    print(page.provider, page.success, page.route)
    print(page.markdown[:1000])

asyncio.run(main())
```

## Use from CLI

```bash
scrape-gateway url https://example.com
scrape-gateway url https://example.com --country us --render-js --premium
scrape-gateway run urls.txt --country us
```

## Provider env vars

```bash
SCRAPEDRIVE_API_KEY=...
SCRAPE_DO_TOKEN=...
SCRAPINGBEE_API_KEY=...
SCRAPERAPI_API_KEY=...
```

## Current provider adapters

| Provider | Status | Notes |
|---|---:|---|
| raw_http | working | cheapest path, no JS/country/premium |
| ScrapeDrive | scaffold | endpoint/params need verification against private/public docs |
| Scrape.do | scaffold | API mode with `token`, `url`, `geoCode`, `super` |
| ScrapingBee | scaffold | HTML API with `api_key`, `render_js`, `premium_proxy`, `country_code` |
| ScraperAPI | scaffold | sync endpoint with `api_key`, `render`, `premium`, `country_code`, `screenshot` |

## Design

```txt
cache → remembered domain route → cheapest provider → paid fallback → artifact storage
```

The OSS project should stay provider-neutral. ScrapeDrive can be the best-supported production fallback without making the router feel vendor-captured.
