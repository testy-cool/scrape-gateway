# Commands

## `sgw url` — Scrape a single page

Tries providers from cheapest to most expensive until one succeeds. Results are cached locally so repeat scrapes are instant and free. Domain memory remembers which provider worked.

```bash
sgw url https://example.com                    # basic scrape
sgw url https://example.com --render-js        # JS-heavy SPA
sgw url https://example.com -p scrapedrive     # force a provider
sgw url https://example.com --no-cache         # bypass cache
sgw url https://example.com -f markdown        # get markdown instead of HTML
sgw url https://example.com --tier advanced    # force ScrapeDrive tier
sgw url https://example.com --meta             # extract OG metadata inline
sgw url https://example.com --debug-artifacts  # save failed response bodies
```

## `sgw extract` — Pull structured data from listing pages

Finds repeated elements on a page (product cards, article lists, search results) and pulls structured data from each one as JSON, CSV, or a rich table.

By default, an LLM picks the best pattern and gives fields semantic names (e.g., renaming the CSS class `instock` to `availability`). This costs a few cents the first time, then it's cached per domain forever — repeat extractions are free.

```bash
sgw extract https://books.toscrape.com              # auto-detect pattern, JSON output
sgw extract https://books.toscrape.com -f csv        # CSV output
sgw extract https://books.toscrape.com -f rich       # visual table
sgw extract https://books.toscrape.com -s "ol > li"  # manual CSS selector
sgw extract https://books.toscrape.com --no-llm      # skip LLM, use heuristic
sgw extract https://books.toscrape.com -n 5          # first 5 rows only
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

The LLM never sees the full HTML — just a summary of detected patterns. One call per new domain, cached forever. Without an LLM configured, `sgw extract` falls back to a heuristic (picks the most-repeated pattern).

LLM setup (optional):
```bash
pip install llm
llm keys set openai   # or whatever provider you use
```

## `sgw detect` — Reconnaissance before extraction

Scans a page for repeated elements and reports what it finds: CSS selectors, repeat counts, and sample content. Also spots prices, dates, and emails. Run this first to understand a page's structure before extracting.

```bash
sgw detect https://books.toscrape.com
sgw detect https://example.com --render-js
```

## `sgw links` — Find and index all links on a page

Finds all links, assigns each a numbered index, and groups them by semantic location (navigation, main content, footer, sidebar). Use `sgw follow` to scrape a specific link by its number.

```bash
sgw links https://example.com               # rich table
sgw links https://example.com -f compact    # tree view (LLM-friendly)
sgw links https://example.com -f json       # pipe to jq
sgw links https://example.com --limit 20    # first 20 only
```

## `sgw follow` — Navigate by link index

Two scrapes in one command: loads the page to get links (from cache if available), then scrapes the link you pick by index.

```bash
sgw links https://example.com         # see indices
sgw follow https://example.com 3      # scrape link #3
```

## `sgw recipe` — Replay saved workflows

Write URLs, scrape settings, and extraction config once as YAML, then replay with one command. Results from multiple URLs are combined into a single output file.

```bash
sgw recipe books.yml                  # run the recipe
sgw recipe books.yml --dry-run        # preview without scraping
sgw recipe books.yml -o results.csv   # override output path
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

## `sgw run` — Batch scrape from a file

Scrapes each URL in a text file and shows a summary table.

```bash
sgw run urls.txt
sgw run urls.txt --render-js -p scrapedrive
sgw run urls.txt -p scrapedrive --tier advanced
```

## `sgw meta` — Extract OpenGraph metadata

Extracts OG tags as clean JSON. Pipe to `jq` or use in scripts.

```bash
sgw meta https://example.com
sgw meta https://example.com --render-js
sgw meta https://example.com 2>/dev/null | jq '.["og:image"]'
```

## `sgw history` — Track page changes over time

Every scrape fingerprints the page (title, link count, headings, text length). This command shows the timeline: when you scraped, which provider worked, and what changed between scrapes.

```bash
sgw history https://example.com
sgw history https://example.com -n 5    # last 5 scrapes
```

## `sgw telemetry` — Inspect scrape reports

Shows recent telemetry reports with diagnosis codes and recommended actions.

```bash
sgw telemetry                         # table of recent reports
sgw telemetry --json                  # full JSON output
sgw telemetry -d example.com          # filter by domain
sgw telemetry --diagnosis validator_rejected
```

## `sgw cache` — Inspect cached artifacts

Available when the `sg-cache` extension is installed. Useful for hosted MCP deployments with persistent cache state.

```bash
sgw cache stats
sgw cache ls --domain example.com
sgw cache show https://example.com
sgw cache purge --expired --yes
```

## `sgw providers` — See what's available

Lists all providers — built-in, pip packages, and local extensions — with cost, capabilities, and source.

```bash
sgw providers
```

## `sgw extensions` — Browse the extension registry

Shows available community extensions. Install with `sgw extensions <name>`.

```bash
sgw extensions                    # browse the registry
sgw extensions sg-playwright      # install one
```

## `sgw selftest` — Verify installation

Scrapes a few known-safe sites to verify `sgw` is working. Uses only the free raw_http provider, no API keys needed.

```bash
sgw selftest
```
