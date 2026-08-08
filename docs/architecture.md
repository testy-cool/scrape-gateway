# Architecture

## How the router works

```
0. Drop providers that cannot satisfy the request: render_js, premium, screenshot,
   and country are capability-gated. A country inferred from the TLD is a hint and
   does not gate anything; only an explicit country does.
1. Check cache → hit? return cached result, done.
   The key covers the URL, render_js, country, premium, and mobile, so a hit can
   never be a page fetched under different options.
2. Check recent, exact-profile ledger evidence:
   a. Rank providers by total weighted spend per successful page once a provider has
      at least five attempts and two successes.
   b. Give exact billed costs full weight and estimated costs half weight.
   c. Probe one cheaper provider after 24 hours without usable evidence.
   d. Skip providers with enough recent domain failures.
   e. Probe a skipped provider once its failure window expires.
3. Try the resulting provider ladder:
   a. Stop if the next adapter's conservative cost estimate exceeds the remaining budget.
   b. Send request
   c. Validate content (catch Cloudflare, captcha, JS-required pages)
   d. Success? Persist the attempt and provider tier in the ledger. Done.
   e. Failure? Persist the reason and try the next provider.
4. All failed? Return last failure with diagnostics.
```

Domain memory persists in `.scrape-gateway/memory.sqlite`. Routing matches domain,
country, `render_js`, premium, mobile, and screenshot exactly and uses a configurable
seven-day evidence window by default. Missing or invalid credentials and local
configuration failures are excluded from domain evidence. The attempt ledger is the
only record of what was scraped: routing, the per-domain stats at `/v1/stats/{domain}`,
and cost reporting all read it, and the older aggregate tables that once shadowed it
are gone. Databases written before the ledger still open; their leftover tables are
ignored rather than migrated. Cache stores HTML + Markdown artifacts in
`.scrape-gateway/artifacts/`. Both survive across sessions.

The observed score is weighted total spend divided by weighted successes, so failed
attempts count against a provider instead of disappearing from its apparent price.
Five attempts and two successes are the minimum because one lucky success is too thin
to replace the cold-start cost ranks. Provider-reported exact costs receive full weight;
adapter estimates receive half weight because they are useful but less trustworthy.
Explicit request selection, recipe order, and `strategy.provider` stay above learned
cost ordering. Each routing decision and its sample counts are written to the progress
event, run log, and telemetry report.

When `strategy.max_cost_per_url` is set, the router compares the complete current-run
ledger against each adapter's conservative next-call estimate. An attempt that would
cross the ceiling never starts. ScrapeDrive repeats that check before each of its
standard, advanced, and hyperdrive tiers, and Scrapfly's internal ASP `cost_budget` is
clipped to the gateway remainder. A stop returns `FailureReason.BUDGET_EXCEEDED` and a
`budget_stop` metadata object instead of pretending every provider failed.

## Content validation

A 200 OK doesn't mean success. The validator inspects the actual HTML for:

- Cloudflare "checking your browser" challenges
- Captcha / reCAPTCHA walls
- "Please enable JavaScript" placeholders
- Login walls (only on short pages < 8KB to avoid false positives)
- Empty or near-empty responses
- Custom must-contain / must-not-contain rules

When validation fails, the router logs the block type and tries the next provider.

## Telemetry

Every scrape writes a JSON report to `.scrape-gateway/runs/`. Each report includes:

- Full attempt chain with timing
- The routing decision, observed cost per success, and evidence counts
- A distinct budget-exceeded diagnosis and stop details when the ceiling prevents work
- Validation evidence (matched pattern + surrounding snippet)
- Diagnosis code and recommended next action
- Failed response bodies (when `--debug-artifacts` is enabled)

Use `sgw telemetry` to inspect reports.

## AI quality audit

When `evaluation.mode` is `audit`, the router evaluates the final scrape after
deterministic validation. The evaluator sends request context, attempt history,
validation signals, bounded Markdown, and an available screenshot to OpenRouter using
a strict JSON schema. Its output is advisory and cannot change scrape success.

The full evidence bundle lives beside the run report under `evaluation/`; failed
attempt HTML and screenshots are kept at the run root. A stable evidence hash caches
identical content only when provider and attempt context also match. OpenRouter usage
and generation metadata record the actual upstream provider, token count, cost, and
BYOK status when available. `sgw evaluations` aggregates these records into a manual
review queue. The default model and `scrape-usability-v2` prompt are labeled
`calibrated_v1_holdout_2026_07_29` from a one-shot 24-case holdout, but the evaluator
remains advisory because its human-review flag did not catch the holdout error. Any
other model or prompt combination is labeled `uncalibrated_audit`.

## Proxy handling

If `SCRAPE_PROXY_URL` is set, HTTP providers (raw_http, wreq, curl_cffi) route through it. On proxy failure (407, auth errors), providers automatically retry direct — a broken proxy doesn't block scraping entirely. The router stops escalating on proxy errors since the problem is configuration, not the target site.

## Project structure

```
src/scrape_gateway/
  cli.py          — Typer CLI (all sgw commands)
  router.py       — Provider routing, fallback, validation
  discovery.py    — Extension discovery (built-in providers, command/provider entry points, local dirs)
  provider.py     — ProviderAdapter base class
  memory.py       — Domain memory (SQLite) + extraction pattern cache
  scrapingevals.py — Privacy-safe passive evidence feed and cursor contract
  cache.py        — HTML/Markdown artifact cache
  config.py       — YAML config + .env loader
  models.py       — ScrapeRequest, ScrapeResult, FailureReason
  validators.py   — Content validation
  telemetry.py    — Per-run JSON reports and diagnosis
  evaluation.py   — Strict OpenRouter scrape-quality audit and content-hash cache
  providers/      — One adapter per built-in scraping provider
registry.yml      — Official extension registry
tests/            — 190+ unit tests
examples/         — Sample recipes and extension template
```

## Passive ScrapingEvals feed

The same append-only attempt ledger used for cost-aware routing can be exported without
running another scrape. `sgw scrapingevals` combines ledger rows with an allowlisted
subset of run telemetry, strips content and sensitive context, hashes available
artifacts, and emits stable source/event IDs plus a high-watermark cursor.

The resulting `scrapingevals.sgw-observations/v1` document is always marked as a
review-required operational observation. ScrapingEvals owns validation, idempotent
import, acknowledgement, and human promotion; SGW never turns opportunistic traffic
into public benchmark claims. See [the feed contract](scrapingevals-feed.md).
