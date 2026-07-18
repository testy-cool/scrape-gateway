# scrape-gateway (`sgw`)

[![ci](https://github.com/testy-cool/scrape-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/testy-cool/scrape-gateway/actions/workflows/ci.yml)
[![version](https://img.shields.io/badge/version-0.13.0-blue)](https://github.com/testy-cool/scrape-gateway/releases/latest)
[![license](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

<p align="center">
  <img src="docs/terminal.svg" alt="sgw demo — free providers fail, paid provider succeeds, next time it remembers" width="720">
</p>

One command, seven providers. Free ones tried first, paid ones only when needed. Domain memory skips the trial-and-error on repeat visits.

## Quick start

```bash
git clone https://github.com/testy-cool/scrape-gateway.git
cd scrape-gateway
pip install -e .
cp .env.example .env   # add API keys (optional — free providers work without any)

sgw selftest           # verify installation
sgw url https://example.com
```

## Browser console

The HTTP service includes a browser console at `/`. The MCP endpoint stays at
`/mcp`, so one process serves both surfaces.

Run the console locally:

```bash
pip install -e ".[mcp]"
export SGW_MCP_TOKEN="choose-a-local-token"  # optional for local use
SGW_MCP_PORT=8100 SGW_MCP_URL=http://localhost:8100 \
  python -m scrape_gateway.mcp_server
```

Open `http://localhost:8100/`. If `SGW_MCP_TOKEN` is set, enter the same token
in the connection dialog. The browser keeps it in the current tab's
`sessionStorage` and sends it only in API authorization headers.

You can use the console to:

- Browse the latest 500 runs in a live-refreshing trace inventory and filter by URL, provider, scrape status, or audit status.
- Refresh or reopen the console while a scrape is running without losing it; active requests are tracked by the service and restored into the trace inventory.
- Follow provider attempts, validation, screenshot delivery, AI evaluation, and evidence persistence as they happen; active runs poll once per second and retain their live timeline across a refresh.
- Select any trace step to inspect its outcome, timing, summary, and complete saved attributes. Recorded durations use waterfall bars; steps without timing data are clearly marked as order-only.
- Open scraped Markdown/HTML, AI checks and improvement signals, saved artifacts and screenshots, or the complete raw telemetry report without running scraped page scripts.
- Start a scrape from the `New scrape` dialog with an evaluation goal, JavaScript rendering, screenshot evidence, mobile rendering, premium routing, ad blocking, or a cache bypass.
- Open `Gateway settings` to enable or disable providers globally and set the default, per-provider, and AI-evaluation timeouts used by subsequent console and MCP runs.

The console reads the same `.scrape-gateway/runs/` evidence used by
`sgw evaluations`. It does not change prompts, validators, or routing based on
an AI verdict. Console-owned routing settings are kept locally in
`.scrape-gateway/operator-settings.yml`; they override the base YAML without
rewriting it and should remain on the gateway's persistent volume rather than
being committed.

## Commands

| Command | What it does |
|---|---|
| `sgw url <url>` | Scrape one page through the provider chain |
| `sgw extract <url>` | Pull structured data (JSON/CSV) from listing pages |
| `sgw detect <url>` | Recon — find repeated elements before extracting |
| `sgw links <url>` | Index all links on a page |
| `sgw follow <url> <n>` | Scrape link #n from a page |
| `sgw recipe <file>` | Replay a saved YAML workflow |
| `sgw run <file>` | Batch scrape URLs from a text file |
| `sgw meta <url>` | Extract OpenGraph metadata as JSON |
| `sgw history <url>` | Show scrape timeline and page changes |
| `sgw telemetry` | Inspect reports or aggregate routing metrics with `--summary` |
| `sgw evaluations` | Aggregate AI quality audits and review recurring failures |
| `sgw providers` | List all available providers |
| `sgw extensions` | Browse/install community extensions |
| `sgw selftest` | Verify installation with known-safe sites |

Full usage and examples: [docs/commands.md](docs/commands.md)

## Providers

Seven built-in providers plus the bundled Browserless extension. The router tries
lower cost ranks first.

| Provider | JS | Screenshot | Markdown | Country | CAPTCHA | Cost tier |
|---|---|---|---|---|---|---|
| `raw_http` | No | No | No | No | No | Free · rank 0 |
| `wreq` | No | No | No | No | No | Free · rank 2 |
| `curl_cffi` | No | No | No | No | No | Free · rank 3 |
| `browserless` | Yes | Yes | No | No | No | Self-hosted · rank 20 |
| `scrapedrive` | Yes | Yes | Yes | Yes | Yes (hyperdrive) | Paid · rank 25 |
| `scrape_do` | Yes | No | No | Yes | Yes (automatic) | Paid · rank 30 |
| `scrapingbee` | Yes | No | No | Yes | Yes (premium proxy) | Paid · rank 35 |
| `scraperapi` | Yes | Yes | No | Yes | Yes (retry/bypass) | Paid · rank 40 |

The feature columns reflect what each adapter declares and wires into `sgw`, not
every feature sold by the upstream service. The router can convert successful
HTML to Markdown after capture, so `-f markdown` still works when native Markdown
is `No`. CAPTCHA means handling an access challenge, not solving a CAPTCHA embedded
in a form. The service-specific claims are documented by
[ScrapeDrive](https://scrapedrive.com/docs/),
[Scrape.do](https://scrape.do/documentation/),
[ScrapingBee](https://www.scrapingbee.com/documentation/remote-mcp/), and
[ScraperAPI](https://docs.scraperapi.com/resources/faq/anti-bots-and-captchas).
Browserless itself offers CAPTCHA products, but this adapter uses only its
`/content` and `/screenshot` REST endpoints; the
[open-source Browserless image](https://docs.browserless.io/enterprise/open-source)
does not include CAPTCHA solving.

Add API keys in `.env` to enable paid providers. Browserless uses its own service
URL and token. Without those credentials, `sgw` uses the three free providers.

## AI scrape-quality audits

Enable the optional audit evaluator to have OpenRouter's
`google/gemini-3.1-flash-lite` judge the deterministic signals and saved Markdown,
plus a screenshot when one was requested and captured:

```yaml
evaluation:
  mode: audit
  model: google/gemini-3.1-flash-lite
  include_screenshot: true
```

```bash
export OPENROUTER_API_KEY=...
sgw url https://example.com/products \
  --evaluation-goal "Capture every visible product and price" \
  --screenshot
sgw evaluations
```

Each run keeps the evaluator request, strict JSON response, final HTML and Markdown,
screenshot when available, hashes, token/cost data, and OpenRouter generation metadata
under `.scrape-gateway/runs/<run-id>/evaluation/`. The judge returns a binary usability
verdict plus categorical access, goal-coverage, extractability, and visual-state checks.
Audit failures never turn a successful scrape into a failed scrape. The judge is explicitly
uncalibrated and audit-only; `sgw evaluations` builds a review queue but does not modify
prompts, validators, or routing.

## Extend it

Drop a provider `.py` file in `~/.config/scrape-gateway/providers/`, drop a command `.py` file in `~/.config/scrape-gateway/commands/`, or install from the registry with `sgw extensions`. See [docs/extensions.md](docs/extensions.md).

## Python API

```python
from scrape_gateway import ScrapeGateway, ScrapeRequest

gw = ScrapeGateway.from_config()
result = await gw.scrape(ScrapeRequest("https://example.com"))
```

More: [docs/python-api.md](docs/python-api.md)

## Docs

- [Commands](docs/commands.md) — full reference with examples
- [Architecture](docs/architecture.md) — how the router, cache, and memory work
- [Configuration](docs/configuration.md) — YAML config and `.env` setup
- [Extensions](docs/extensions.md) — writing custom providers
- [Python API](docs/python-api.md) — using sgw as a library
- [Providers](docs/providers.md) — provider details and API mapping
