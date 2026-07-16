# Architecture

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
review queue. The current binary usability judge is labeled `uncalibrated_audit`;
calibration against human labels is required before using its verdicts as an automated
acceptance gate.

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
