# scrape-gateway (`sg`)

```bash
$ sg url https://hard-to-scrape-store.com
  [raw_http]    403 0.3s → ✗ blocked
  [wreq]        403 0.5s → ✗ blocked
  [scrapedrive] 200 2.1s → ✓ pass (advanced tier)

# Next time — sg remembers what worked:
$ sg url https://hard-to-scrape-store.com/other-page
  [scrapedrive] 200 1.8s → ✓ pass (advanced tier)    ← skipped free providers, went straight here
```

One `sg url` call. Seven providers behind it. It tried the free ones first, they got blocked, ScrapeDrive's advanced tier worked, and now it remembers — every future scrape of that domain skips straight to what works.

---

**scrape-gateway is a unified interface to multiple scraping providers.** You write `sg url <anything>` and it figures out which provider to use, handles failures, validates the content isn't a Cloudflare challenge page, and remembers what worked per domain. It also extracts structured data from listing pages, but the gateway is the core.

## Why this exists

You have 3-4 scraping APIs. Each has its own SDK, its own auth, its own quirks. Some sites work with free HTTP requests. Some need residential proxies. Some need full browser rendering. You end up writing if/else chains, retry logic, and provider-switching code for every project.

`sg` is that code, written once:

1. **One interface, many providers.** 7 providers (3 free, 4 paid) behind a single `sg url` command. Add your API keys and the router handles the rest.
2. **Cheapest-first routing.** Free providers are tried before paid ones. You only pay when the free ones fail.
3. **Content validation.** A 200 OK doesn't mean success — the page might be a Cloudflare challenge, a captcha wall, or a "please enable JavaScript" placeholder. `sg` catches all of these and retries with the next provider.
4. **Domain memory.** After one successful scrape, `sg` remembers which provider and tier worked for that domain. Next scrape skips the trial-and-error entirely.
5. **Structured extraction.** Once you have the HTML, `sg extract` pulls structured data (JSON/CSV) from repeated page elements — product cards, article lists, search results — with optional LLM-powered pattern picking.

## Quick start

```bash
# Install
git clone https://github.com/testy-cool/scrape-gateway.git
cd scrape-gateway
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env   # add your API keys

# Verify it works (no API keys needed)
sg selftest

# Scrape a page
sg url https://example.com

# Extract structured data from a listing page
sg extract https://books.toscrape.com
```

## Commands

### `sg url` — Scrape a single page

Tries providers from cheapest to most expensive until one succeeds. Results are cached locally so repeat scrapes are instant and free. Domain memory remembers which provider worked.

```bash
sg url https://example.com                    # basic scrape
sg url https://example.com --render-js        # JS-heavy SPA
sg url https://example.com -p scrapedrive     # force a provider
sg url https://example.com --no-cache         # bypass cache
sg url https://example.com -f markdown        # get markdown instead of HTML
```

### `sg extract` — Pull structured data from listing pages

The main data extraction command. Finds repeated elements on a page (product cards, article lists, search results) and pulls structured data from each one as JSON, CSV, or a rich table.

By default, an LLM picks the best pattern and gives fields semantic names (e.g., renaming the CSS class `instock` to `availability`). This costs a few cents the first time, then it's cached per domain forever — repeat extractions are free.

```bash
sg extract https://books.toscrape.com              # auto-detect pattern, JSON output
sg extract https://books.toscrape.com -f csv        # CSV output
sg extract https://books.toscrape.com -f rich       # visual table
sg extract https://books.toscrape.com -s "ol > li"  # manual CSS selector
sg extract https://books.toscrape.com --no-llm      # skip LLM, use heuristic
sg extract https://books.toscrape.com -n 5          # first 5 rows only
```

Example output:
```json
[
  {
    "title": "A Light in the Attic",
    "href": "catalogue/a-light-in-the-attic_1000/index.html",
    "image": "media/cache/2c/da/2cdad67c44b002e7ead0cc35693c0e8b.jpg",
    "price": "£51.77",
    "availability": "In stock"
  }
]
```

### `sg detect` — Reconnaissance before extraction

Scans a page for repeated elements and reports what it finds: CSS selectors, repeat counts, and sample content. Also spots prices, dates, and emails. Run this first to understand a page's structure before extracting.

```bash
sg detect https://books.toscrape.com
sg detect https://example.com --render-js
```

### `sg links` — Find and index all links on a page

Finds all links, assigns each a numbered index, and groups them by semantic location (navigation, main content, footer, sidebar). Use `sg follow` to scrape a specific link by its number.

```bash
sg links https://example.com               # rich table
sg links https://example.com -f compact    # tree view (LLM-friendly)
sg links https://example.com -f json       # pipe to jq
sg links https://example.com --limit 20    # first 20 only
```

### `sg follow` — Navigate by link index

Two scrapes in one command: loads the page to get links (from cache if available), then scrapes the link you pick by index. Like browsing from the terminal.

```bash
sg links https://example.com         # see indices
sg follow https://example.com 3      # scrape link #3
```

### `sg recipe` — Replay saved workflows

Saves you from retyping the same command with all its flags. Write URLs, scrape settings, and extraction config once as YAML, then replay with one command. Results from multiple URLs are combined into a single output file.

```bash
sg recipe books.yml                  # run the recipe
sg recipe books.yml --dry-run        # preview without scraping
sg recipe books.yml -o results.csv   # override output path
```

Recipe file format:
```yaml
urls:
  - https://books.toscrape.com
  - https://books.toscrape.com/catalogue/page-2.html

scrape:
  provider: scrapedrive
  country: us
  render_js: true

extract:
  selector: "ol.row > li"
  format: json
  limit: 20

output: results.json
```

### `sg run` — Batch scrape from a file

Scrapes each URL in a text file and shows a summary table. If you also need to extract data, use `sg recipe` instead — it combines scraping and extraction.

```bash
sg run urls.txt
sg run urls.txt --render-js -p scrapedrive
```

### `sg history` — Track page changes over time

Every scrape fingerprints the page (title, link count, headings, text length). This command shows the timeline: when you scraped, which provider worked, and what changed between scrapes.

```bash
sg history https://example.com
sg history https://example.com -n 5    # last 5 scrapes
```

### `sg selftest` — Verify installation

Scrapes a few known-safe sites to verify `sg` is working. Uses only the free raw_http provider, no API keys needed.

```bash
sg selftest
```

## How the router works

```
1. Check cache → hit? return cached result, done.
2. Check domain memory → know a provider that worked before? try it first.
3. Try providers cheapest-first:
   a. Send request
   b. Validate content (catch Cloudflare, captcha, JS-required pages)
   c. Success? Remember provider + tier in domain memory. Done.
   d. Failure? Log it, try next provider.
4. All failed? Return last failure with diagnostics.
```

Domain memory persists in `.scrape-gateway/memory.sqlite`. Cache stores HTML + Markdown artifacts in `.scrape-gateway/artifacts/`. Both survive across sessions.

## Providers

7 providers, 3 free and 4 paid. The router tries free ones first.

| Provider | Cost | JS Rendering | Geo-targeting | Anti-bot | Notes |
|---|---|---|---|---|---|
| `raw_http` | free | no | no | none | Plain HTTP GET. Works for simple sites. |
| `wreq` | free | no | no | TLS fingerprinting | Impersonates real browser TLS. Better than raw_http for bot-detection. |
| `curl_cffi` | free | no | no | TLS fingerprinting | Similar to wreq, different implementation. |
| `scrapedrive` | paid | yes | yes | full | 3 tiers: standard ($), advanced ($$), hyperdrive ($$$). Auto-escalates. |
| `scrape_do` | paid | yes | yes | residential proxies | Good fallback. |
| `scrapingbee` | paid | yes | yes | premium proxies | Another fallback option. |
| `scraperapi` | paid | yes | yes | premium proxies | Supports screenshots. |

### API keys

Add to `.env` (copy from `.env.example`):

```bash
SCRAPEDRIVE_API_KEY=your_key_here
SCRAPE_DO_TOKEN=your_token_here
SCRAPINGBEE_API_KEY=your_key_here
SCRAPERAPI_API_KEY=your_key_here
```

`sg` works without any paid API keys — it will use the free providers only. Add paid keys when you need JS rendering, geo-targeting, or anti-bot bypass.

## LLM integration (optional)

`sg extract` optionally uses an LLM to pick the best pattern on a page and name fields semantically. This uses [Simon Willison's `llm` CLI](https://github.com/simonw/llm) under the hood — whatever model you have configured there is what `sg` uses.

```bash
# Setup (one time)
pip install llm
llm keys set openai              # or whatever provider you use

# Then sg extract just works
sg extract https://books.toscrape.com   # LLM picks the product grid, not the sidebar nav
```

The LLM never sees the full HTML — just a summary of detected patterns. One call per new domain, cached forever. Without an LLM configured, `sg extract` falls back to a heuristic (picks the most-repeated pattern).

## Use from Python

```python
import asyncio
from scrape_gateway import ScrapeGateway, ScrapeRequest

async def main():
    gateway = ScrapeGateway.from_config()
    result = await gateway.scrape(ScrapeRequest(
        "https://example.com",
        country="us",
        render_js=False,
    ))
    print(result.provider, result.success, result.route)
    print(result.html[:500])

asyncio.run(main())
```

## Configuration

Optional YAML config at `scrape-gateway.yml`:

```yaml
cache:
  ttl: 24h
  root: .scrape-gateway/artifacts

providers:
  - raw_http
  - wreq
  - curl_cffi
  - name: scrapedrive
    enabled: true
  - name: scraperapi
    enabled: false

strategy:
  mode: cheapest_successful
  provider: scrapedrive       # override: always try this first
```

`sg` works with zero configuration. The YAML file is for overriding defaults (disabling providers, changing cache TTL, forcing a preferred provider).

## Testing

```bash
# Unit tests (no network, no API keys)
pytest tests/ -k "not test_scrapedrive_live"

# Full suite including live API tests
pytest tests/

# HTML report with plain-language test descriptions
pytest tests/ --html=output/test-report.html --self-contained-html
```

112 unit tests covering: caching, config parsing, error classification, data extraction, domain memory, provider adapters, router logic, and content validation. Each test has a human-readable description explaining what it checks and why.

## Project structure

```
src/scrape_gateway/
  cli.py          — Typer CLI (all sg commands)
  router.py       — Provider routing, fallback, validation
  memory.py       — Domain memory (SQLite) + extraction pattern cache
  cache.py        — HTML/Markdown artifact cache
  config.py       — YAML config + .env loader
  models.py       — ScrapeRequest, ScrapeResult, FailureReason
  validators.py   — Content validation (Cloudflare, captcha, JS detection)
  providers/      — One adapter per scraping provider
    raw_http.py, wreq_http.py, curl_cffi_http.py,
    scrapedrive.py, scrape_do.py, scrapingbee.py, scraperapi.py
tests/            — 112 tests with HTML report support
examples/         — Sample recipe files
```
