---
name: scrape-gateway
description: Use when the user asks to "scrape a URL", "extract data from a site", "set up sgw", "add a scraping provider", "write an sgw extension", "sgw url", "sgw extract", "sgw recipe", "sgw evaluations", "sgw providers", "sgw extensions", or needs to scrape web pages through multiple providers with automatic fallback, audit scrape quality, extract structured data from listing pages, or build custom scraping providers.
---

# scrape-gateway (sgw)

Unified CLI for scraping web pages through multiple providers with cheapest-first routing, content validation, domain memory, and structured data extraction.

**Repo**: https://github.com/testy-cool/scrape-gateway
**Binary**: `sgw` (installed via `uv tool install`)
**Config**: `scrape-gateway.yml` (project root or CWD)
**API keys**: `.env` (copy from `.env.example`)
**Extensions dir**: `~/.config/scrape-gateway/providers/`
**Remote MCP**: `https://sgw.voidxd.cloud/mcp` (Coolify app `sgw-mcp`)

## Setup

```bash
git clone https://github.com/testy-cool/scrape-gateway.git
cd scrape-gateway
pip install -e .
cp .env.example .env  # add your API keys
sgw selftest
```

wreq and curl_cffi (free anti-detect HTTP) are included as required dependencies.

## Providers

7 built-in, all discovered via entry points. Extensions use the same mechanism.

| Provider | Cost Rank | Free | JS | Anti-bot |
|---|---|---|---|---|
| `raw_http` | 0 | yes | no | none |
| `wreq` | 2 | yes | no | TLS fingerprinting |
| `curl_cffi` | 3 | yes | no | TLS fingerprinting |
| `scrapedrive` | 25 | no | yes | full (3 tiers) |
| `scrape_do` | 30 | no | yes | residential proxies |
| `scrapingbee` | 35 | no | yes | premium proxies |
| `scraperapi` | 40 | no | yes | premium proxies |

Router tries cheapest first. Domain memory skips to what worked last time.

## Core Commands

### sgw url — Scrape one page

```bash
sgw url <url>                        # auto-route
sgw url <url> --render-js            # force JS rendering
sgw url <url> -p scrapedrive         # force provider
sgw url <url> --no-cache             # skip cache
sgw url <url> -f markdown            # markdown output
sgw url <url> --country us           # geo-target
sgw url <url> --premium              # use highest tier
sgw url <url> --screenshot           # request visual evidence
sgw url <url> --evaluation-goal "Capture visible products and prices"
```

### sgw extract — Structured data from listing pages

```bash
sgw extract <url>                    # auto-detect pattern, JSON output
sgw extract <url> -f csv             # CSV output
sgw extract <url> -f rich            # visual table
sgw extract <url> -s "ol > li"       # manual CSS selector
sgw extract <url> --no-llm           # skip LLM pattern picking
sgw extract <url> -n 5               # limit rows
```

LLM picks the main content pattern and names fields semantically. Cached per domain — first call costs a few cents, repeat calls are free.

### sgw detect — Reconnaissance

```bash
sgw detect <url>                     # find repeated elements
```

Shows CSS selectors, repeat counts, sample content. Run before `sgw extract` to understand page structure.

### sgw links / sgw follow — Navigate

```bash
sgw links <url>                      # indexed link list
sgw links <url> -f compact           # tree view (LLM-friendly)
sgw follow <url> 3                   # scrape link #3
```

### sgw recipe — Replay workflows

```yaml
# books.yml
urls:
  - https://books.toscrape.com
  - https://books.toscrape.com/catalogue/page-2.html
scrape:
  provider: scrapedrive
  render_js: true
extract:
  selector: "ol.row > li"
  format: json
output: results.json
```

```bash
sgw recipe books.yml                 # run it
sgw recipe books.yml --dry-run       # preview
```

### sgw providers — List available providers

```bash
sgw providers                        # shows all: built-in + extensions
```

### sgw extensions — Browse/install extensions

```bash
sgw extensions                       # browse registry
sgw extensions sg-playwright         # install one
```

### sgw cache — Inspect cached artifacts

Available when `sg-cache` is installed:

```bash
sgw cache stats                      # size/count/provider summary
sgw cache ls --domain example.com    # cached entries for a domain
sgw cache show <url-or-key>          # print cached markdown
sgw cache purge --expired --yes      # delete expired entries
```

### sgw evaluations — Review AI scrape-quality audits

Enable the optional OpenRouter evaluator in `scrape-gateway.yml`:

```yaml
evaluation:
  mode: audit
  model: google/gemini-3.1-flash-lite
  include_screenshot: true
```

Set `OPENROUTER_API_KEY`, then inspect saved results:

```bash
sgw url <url> --evaluation-goal "Capture the main product listing" --screenshot
sgw evaluations
sgw evaluations --format json
```

Audit mode saves the exact evaluator request, strict JSON response, final HTML and
Markdown, screenshot when captured, hashes, attempts, tokens, OpenRouter billed cost,
BYOK upstream cost, and provider metadata under
`.scrape-gateway/runs/<run-id>/evaluation/`. Evaluator failures never change primary
scrape success. Identical evidence and provider context reuse a content-addressed
evaluation cache.

The binary judge is `uncalibrated_audit`: use its categorical checks, structured
issue/root-cause counts, and review queue to find recurring improvements, but do not
automatically change prompts, validators, or routing from its verdicts.

## Remote MCP Ops

Scrape Gateway also runs as a hosted MCP server on `coolify-gen2`. For deployment, proxy, token, persistence, and smoke-test details, read:

```text
docs/mcp-coolify-ops.md
```

Key rule: Caddy must route to the stable Docker network alias `sgw-mcp:8100`, not a container IP or timestamped Coolify container name. Normal redeploys change the container name.

Use this helper after proxy/deploy changes:

```bash
scripts/sgw-mcp-smoke.sh
```

## Writing Extensions

Drop a `.py` file in `~/.config/scrape-gateway/providers/`:

```python
from scrape_gateway import ProviderAdapter, ScrapeRequest, ScrapeResult, FailureReason

class MyProvider(ProviderAdapter):
    name = "my_api"
    cost_rank = 10
    capabilities = frozenset({"html"})
    install_requires = ["some-package"]  # auto-installed on first use

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        # Return ScrapeResult with success=True and html=... on success
        # Return ScrapeResult with success=False and failure_reason=... on failure
        ...
```

Required attributes: `name` (str), `cost_rank` (int), `capabilities` (frozenset).
Required method: `async scrape(request: ScrapeRequest) -> ScrapeResult`.
Optional: `install_requires` (list[str]) — deps auto-installed on first load.

For pip-distributable extensions, declare an entry point:
```toml
[project.entry-points."scrape_gateway.providers"]
my_provider = "my_package:MyProvider"
```

## Python API

```python
import asyncio
from scrape_gateway import ScrapeGateway, ScrapeRequest

async def main():
    gw = ScrapeGateway.from_config()
    result = await gw.scrape(ScrapeRequest("https://example.com"))
    print(result.provider, result.success, result.html[:200])

asyncio.run(main())
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| All providers fail with `js_required` | Site needs browser rendering | `--render-js` or add ScrapeDrive key |
| `wreq`/`curl_cffi` show `PROVIDER_ERROR` | Not installed | `uv tool install --reinstall -e . --with wreq --with curl_cffi` |
| ScrapeDrive returns 401 | API key not loaded | Check `.env` has `SCRAPEDRIVE_API_KEY`, verify with `sgw selftest` |
| `sgw` works in project dir but not elsewhere | Config was CWD-relative | Update to latest — fixed to fall back to project root |
| Extension not showing in `sgw providers` | File not in right dir or has errors | Check `~/.config/scrape-gateway/providers/`, run `sgw providers` for error messages |
| `sgw extract` picks wrong pattern | LLM chose nav instead of content | Use `-s "selector"` to specify manually, or `--no-llm` for heuristic |
| Evaluation is skipped | OpenRouter key is unavailable | Set `OPENROUTER_API_KEY` or add the `openrouter` key to the `llm` CLI key store |
