---
name: scrape-gateway
description: Use when the user asks to "scrape a URL", "extract data from a site", "set up sgw", "add a scraping provider", "write an sgw extension", "sgw url", "sgw extract", "sgw recipe", "sgw evaluations", "sgw cost", "sgw providers", "sgw extensions", "what did scraping cost", "why did that scrape escalate", or needs to scrape web pages through multiple providers with automatic fallback, audit scrape quality, control what AI evaluation is paid for, inspect recorded scrape spend, extract structured data from listing pages, or build custom scraping providers.
---

# scrape-gateway (sgw)

Unified CLI for scraping web pages through multiple providers with cheapest-first routing, content validation, domain memory, and structured data extraction.

**Repo**: https://github.com/testy-cool/scrape-gateway
**Binary**: `sgw` (installed via `uv tool install`)
**Config**: `scrape-gateway.yml` (project root or CWD)
**API keys**: `.env` (copy from `.env.example`)
**Extensions dir**: `~/.config/scrape-gateway/providers/`
**Remote MCP**: set `SGW_MCP_URL` to your own deployment's `/mcp` endpoint

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

16 built-in, all discovered via entry points. Extensions use the same mechanism.

| Provider | Cost Rank | Free | JS | Screenshot | Anti-bot |
|---|---|---|---|---|---|
| `raw_http` | 0 | yes | no | no | none |
| `wreq` | 2 | yes | no | no | TLS fingerprinting |
| `curl_cffi` | 3 | yes | no | no | TLS fingerprinting |
| `jina_reader` | 8 | yes | yes | no | none |
| `crawl4ai` | 18 | yes | yes | yes | none (self-hosted) |
| `spider_cloud` | 24 | no | yes | no | premium |
| `scrapedrive` | 25 | no | yes | yes | premium (3 tiers) |
| `firecrawl` | 26 | no | yes | yes | premium |
| `scrape_do` | 30 | no | yes | no | premium |
| `scrapfly` | 32 | no | yes | no | premium |
| `scrapingant` | 33 | no | yes | no | premium |
| `zenrows` | 34 | no | yes | no | premium |
| `scrapingbee` | 35 | no | yes | no | premium |
| `scraperapi` | 40 | no | yes | yes | premium |
| `oxylabs` | 45 | no | yes | yes | premium |
| `brightdata` | 50 | no | yes | yes | premium |

The five free providers declare `is_free = True`; the other eleven implement
`estimated_cost_units`. Nine declare `country`: `scrapedrive`, `firecrawl`,
`scrape_do`, `scrapfly`, `scrapingant`, `zenrows`, `scrapingbee`, `scraperapi`,
`oxylabs`.

Router tries cheapest first, then switches to observed cost once a provider has 5
attempts and 2 successes on the same request profile. Domain memory **skips providers
with bad history for that domain** — it does not jump to a previous winner. Run
`sgw providers` to see the live table, which also includes any installed extensions.

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
sgw url <url> --screenshot           # visual evidence, saved into the run folder
sgw url <url> --screenshot shot.jpg  # write the image to an explicit path
sgw url <url> --evaluation-goal "Capture visible products and prices"
```

The success panel prints the saved screenshot path. `sgw run --screenshot <dir>` writes
one input-ordered, URL-slugged image per captured result into an existing directory.

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

### sgw setup — First-run configuration

Interactive: choose which providers to activate and enter API keys. Writes
`scrape-gateway.yml` and `.env` into the current directory. No flags.

```bash
sgw setup
```

### sgw search — Find URLs

Web search across several backends, for when you do not already have the URL.

```bash
sgw search "query"                   # rich output, 10 results
sgw search "query" -b brave -n 25    # auto|bing|duckduckgo|google|brave
sgw search "query" -t w -f urls      # last week, one bare URL per line
sgw search "query" --proxy           # route via SCRAPE_PROXY_URL
```

`-f urls` is the pipe-friendly format: feed it straight into `sgw run`.

### sgw calibrate-evaluator — Score the AI evaluator

Measures the advisory evaluator against the versioned human-labelled corpus.
Replays recorded responses by default, so it costs nothing and needs no API key.

```bash
sgw calibrate-evaluator                    # offline replay, dev split
sgw calibrate-evaluator --split test       # held-out set, claim it once
sgw calibrate-evaluator --live             # actually call the model
sgw calibrate-evaluator --live --concurrency 8
```

`--live` spends OpenRouter credit. `--model` overrides the configured evaluator
model, but note that changing it invalidates the calibration status.

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

### sgw cost — What the scraping actually spent

Every attempt is recorded to SQLite with its provider, route, credits, and whether the
figure is exact or estimated, so a run that escalates through several tiers and then
times out still reports what it burned.

```bash
sgw cost                             # last 30 days by domain and provider
sgw cost --days 7
sgw cost --format json
```

`max_cost_per_url` in `scrape-gateway.yml` is enforced before a tier is attempted, not
after: an escalation forecast above the ceiling is refused with a `budget_exceeded`
failure reason rather than being paid for and reported afterwards.

ScrapeDrive's cost is additive per job — base 5 plus 5 for JavaScript, 5 for a
residential proxy, and 5 for a screenshot — and its `standard`/`advanced`/`hyperdrive`
tiers are internal profiles mapped to the current spec fields (`proxy_pool`,
`render_js`, `proxy_country`, `wait_for`, `wait_ms`, `block_resources`, `timeout_ms`).
Bad-key (401), insufficient-credit (402), validation (422), and rate-limit/backlog
(429) rejections are never charged and are recorded as 0 units; all ScrapeDrive cost is
`estimated` provenance because responses do not report a billed amount.

A ScrapeDrive request whose timeout exceeds 120s is submitted as an async job and
polled, because the sync connection cannot be held past that ceiling. An async job
reports the credits it was charged, so its cost is `exact` rather than `estimated`.
Caller headers only reach the target on that path; the sync host ignores them and the
adapter logs a warning when it drops them.

Setting `SCRAPEDRIVE_AUTO=true` replaces that ladder with ScrapeDrive's own escalation:
one call with a `max_credits` ceiling, charged once for the configuration that
succeeded rather than for every rung climbed, which takes the worst case from 30 credits
to 15. A request naming a `country` still uses the ladder, because Auto refuses
caller-supplied proxy routing.

Domain memory records which provider and profile combinations failed, but it is not a
one-way door — credential failures are excluded, blocks older than 120 days decay, and
skips are isolated per request profile, so a single bad afternoon does not permanently
retire a provider.

### sgw scrapingevals — Stage passive evidence

Normal provider attempts already land in the append-only SQLite ledger. Export a
privacy-safe, review-required batch for ScrapingEvals without running another scrape:

```bash
sgw scrapingevals --out backfill.json --days 0
sgw scrapingevals --out next.json --days 0 --after-ledger-id 1200 --limit 1000
```

The v1 feed has persistent source identity, stable event IDs, and a high-watermark
cursor. It excludes private targets; removes credentials, query strings, fragments,
headers, metadata, content, evaluator prose, and local paths; and omits URL paths by
default. It is an operational-observation inbox, not a comparable benchmark or
automatic publication.

### sgw evaluations — Review AI scrape-quality audits

Enable the optional OpenRouter evaluator in `scrape-gateway.yml`:

```yaml
evaluation:
  mode: selective            # off | audit | selective
  model: google/gemini-3.5-flash-lite
  include_screenshot: true
```

`audit` calls the model on every result. `selective` applies the measured `selective-v1`
runtime gate first and only pays when the deterministic layer is in its unreliable zone —
thin HTML or Markdown, script-dominated pages (JS shells), a password input (login walls),
or a block signature matched against a large content-rich page (false-positive terminology).
On the 60-case corpus it reaches 60/60 correct verdicts with 21 model calls instead of 60,
saving 65% of calls while holding good-page recall at 100%. It is slightly *more* accurate
than always auditing, because the free checks correctly reject a CAPTCHA page the model
passed. Default is `off`; evaluation never influences routing either way.

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

The default `google/gemini-3.5-flash-lite` and `scrape-usability-v2` combination is
`calibrated_v1_holdout_2026_07_29`. Its one-shot 24-case holdout reached 100% TPR,
91.7% TNR, and 96.0% F1, but its human-review flag missed the only verdict error.
Use its categorical checks, structured issue/root-cause counts, and review queue to
find recurring improvements, but do not automatically change prompts, validators, or
routing from its verdicts. Other model or prompt combinations remain
`uncalibrated_audit`.

## Remote MCP Ops

Scrape Gateway can run as a hosted MCP server behind a reverse proxy. Point
`SGW_MCP_URL` at your deployment and `SGW_MCP_TOKEN` at its bearer token; both
the `/mcp` endpoint and the console's `/api/session` require that token.

Key rule when deploying under Coolify: route Caddy to the stable Docker network
alias `sgw-mcp:8100`, not a container IP or a timestamped container name. Normal
redeploys change the container name.

Persist `/data/.scrape-gateway/` via a bind mount so cache artifacts, domain
memory, telemetry runs, and logs survive redeploys.

Use this helper after proxy/deploy changes:

```bash
SGW_MCP_URL=https://your-host/mcp scripts/sgw-mcp-smoke.sh
```

Deployment-specific hostnames, UUIDs, and host aliases belong in your own
private operator notes, not in this repo.

## Writing Extensions

For a new built-in provider, read
[references/adding-built-in-provider.md](references/adding-built-in-provider.md) first.
It is the one-pass surface map for the adapter, discovery, setup, cost ledger, docs,
tests, release, installed CLI, and the separate `sev` integration boundary.

Use an extension for optional heavyweight dependencies or independently shipped
providers. Drop a `.py` file in `~/.config/scrape-gateway/providers/`:

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

Declare cost too: set `is_free = True` if the adapter costs nothing, or override
`estimated_cost_units()` with a conservative upper bound if it can spend money. An
adapter that does neither is treated as unaffordable whenever `max_cost_per_url` is
set, because a paid provider forecast as free defeats the ceiling entirely. With no
ceiling configured it still runs normally.

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
