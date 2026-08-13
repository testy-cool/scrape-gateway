# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.27.2] - 2026-08-13

### Added
- The `sgw url` result panel now shows the absolute paths of the persisted per-run
  `final.html` and `final.md` files (and prints full, copyable `HTML:` / `Markdown:`
  lines after the panel). Paths come from the same telemetry write that creates the
  files, so a path is shown only when the file was actually persisted, and nothing is
  shown when telemetry is disabled.

## [0.27.1] - 2026-08-13

### Fixed
- ScrapeDrive returned HTTP 422 on every request because the adapter still sent the
  removed `scrape_tier` field and the renamed `country_code`, `wait_for_selector`, and
  `extra_wait` fields. The adapter now sends only current spec fields: `standard` /
  `advanced` / `hyperdrive` are internal profiles translated to `proxy_pool`,
  `render_js`, `proxy_country`, `wait_browser`, `wait_for`, `wait_ms`, and
  `block_resources`, and `block_ads` is sent explicitly so the API's blocking default
  no longer overrides the sgw default.

### Changed
- ScrapeDrive cost estimates use the spec's additive credit model — base 5 plus 5 for
  JavaScript, 5 for a residential proxy, and 5 for a screenshot — replacing the old
  1/5/25 tier estimates. Validation (422), rate-limit/backlog (429), and
  insufficient-credit (402) rejections are recorded as 0 units because the spec
  guarantees they are never charged. Responses never report a billed amount, so every
  ScrapeDrive cost now carries `estimated` provenance.

## [0.27.0] - 2026-08-05

### Fixed
- `GET /v1/stats/{domain}` returned `[]` no matter how much scraping had happened. It
  read `domain_provider_stats`, a table only `remember_success`/`remember_failure` wrote
  to, and nothing in the scrape path has called either in a long time. `provider_stats`
  now aggregates the attempt ledger, which is where scrapes are actually recorded.

### Removed
- **Breaking for direct `DomainMemory` users.** `remember_success` and `remember_failure`
  are gone, along with the `domain_provider_stats` and `domain_routes` tables. Nothing in
  the gateway called them. Databases written before the ledger still open; their leftover
  tables are ignored rather than migrated. `provider_stats` returns the same shape except
  `last_success_tier`, which is replaced by `last_success_route` — the ledger records the
  route taken rather than a tier guess.

### Changed
- `docs/architecture.md` documents capability filtering and the full-profile cache key,
  and no longer claims the legacy aggregate tables are retained. `docs/python-api.md`
  records that `country` now restricts routing to country-capable providers.

## [0.26.0] - 2026-08-05

### Added
- `.env` discovery falls back to `~/.config/scrape-gateway/.env`, next to the existing
  extension directories. The installed tool's project root sits inside its venv, so
  outside a checkout sgw previously loaded no keys at all and every keyed provider
  silently dropped off the ladder. A local `.env` still wins, and real environment
  variables still override every file.

## [0.25.1] - 2026-08-05

### Fixed
- 0.25.0's country enforcement combined with TLD auto-detection to route every ccTLD
  domain (.ro, .de, .fr, ...) straight past all free providers to paid ones, because the
  router wrote its TLD guess into `request.country` before routing. The inferred country
  is now a per-attempt hint that only country-capable providers receive; country-blind
  providers serve ccTLD domains exactly as before 0.25.0, the guess no longer reaches
  the cache key, and an explicit `--country` remains binding.

## [0.25.0] - 2026-08-05

Three fixes for the same failure shape: you ask for something, it is quietly not done,
and the result looks like success.

### Fixed
- **A `country` request can no longer be served by a provider that ignores country.**
  `country` was a declared capability that nothing checked, so `--country ro` on a cold
  domain routed to `raw_http` and fetched the page from wherever the process happened to
  be. No error, and a result indistinguishable from a correctly geolocated one.
- **The artifact cache no longer returns pages fetched under different options.** The key
  hashed only the URL and `render_js`; `country`, `premium` and `mobile` are now part of
  it. Options are appended only when set, so plain requests keep their existing keys and
  warm caches survive the upgrade. Country is lowercased and stripped.
- **A preferred provider that gets skipped now says so.** Naming a provider with `-p` and
  receiving a different one was invisible: the end-of-run skip summary only prints when
  nothing succeeded, so a later provider answering hid the skip entirely. All three skip
  paths report it, and it is left on the request as `chosen_provider_skipped`.

### Added
- The Browserless adapter honours `SCRAPE_PROXY_URL`, via
  `?launch={"args":["--proxy-server=..."]}`. Note that Chrome ignores credentials in that
  value and browserless's `externalProxyServer` parameter, which does accept them, exists
  only on the managed cloud service — so a credentialed proxy URL is skipped with the
  reason recorded as `proxy_skipped` rather than silently ignored. Chrome reports proxy
  trouble as a page-load error, so those are detected and retried once without the proxy.

## [0.24.2] - 2026-08-05

### Fixed
- `sgw search` reports a dead backend as a readable message naming the alternatives,
  instead of a rich-formatted `ddgs` traceback with library source lines. The engines
  behind each backend are scraped, so any of them can stop returning results without
  notice; trying another backend is the correct first response and the error now says
  so. At the time of writing `duckduckgo` and `google` return nothing while `bing` and
  `brave` work.
- `docs/SKILL.md` listed 7 providers and claimed that was all of them. It lists all 15,
  with the screenshot capability each one declares. Agents read this file, so the
  omission misinformed every agent session that consulted it.
- `docs/SKILL.md` claimed domain memory routes to a domain's previous winner. It does
  not; it skips providers with bad history for that domain and otherwise leaves the
  cost-ranked order alone. `DomainMemory.preferred_provider` exists but nothing calls it.
- The README version badge showed 0.23.0.

### Changed
- `sgw search --help` no longer names DuckDuckGo as the engine it uses, and documents
  that `--region` mostly reorders results within the language the query is already in.
- `docs/SKILL.md` documents `sgw setup`, `sgw search`, and `sgw calibrate-evaluator`,
  which existed but had no entry.

## [0.24.1] - 2026-08-04

### Fixed
- `ProviderAdapter.install_requires` is now a `ClassVar`. It was a bare mutable class
  attribute shared by every adapter, so an adapter that appended to it rather than
  assigning would have added its dependency to all other providers in the process.

### Changed
- The `ruff` dev dependency requires `>=0.16`; the `<0.16` ceiling is gone. `TRY004` and
  `B008` are disabled with documented reasons, since this codebase raises `ValueError`
  for invalid input by design and `typer.Option` in a default is the Typer idiom.
- Package installs and CLI registry fetches pass `check=False` to `subprocess.run`
  explicitly. Behaviour is unchanged; both already inspected `returncode`.

## [0.24.0] - 2026-08-04

### Changed
- **Breaking for provider extensions.** An adapter must now declare its cost: set
  `is_free = True`, or override `estimated_cost_units()`. An adapter that does neither is
  unpriced, and is skipped rather than run whenever `strategy.max_cost_per_url` is set.
  Previously it inherited an estimate of `0.0` and was forecast as free, so a cost ceiling
  let a paid provider through and the call was billed. Adapters run unchanged when no
  ceiling is configured.
- `budget_stop` metadata gained `estimate_state`, distinguishing `unpriced` from
  `too_expensive` and `invalid`, each with its own error message. `next_attempt_cost_units`
  is now `null` unless a real forecast exists.

### Fixed
- `raw_http`, `wreq`, `curl_cffi`, `crawl4ai`, and `jina_reader` now declare `is_free`.
  All five are genuinely free and were relying on the old default.

## [0.23.1] - 2026-08-04

### Changed
- `scripts/sgw-mcp-smoke.sh` now requires `SGW_MCP_URL` instead of defaulting to a
  specific deployment, and only needs `ssh` when `SGW_MCP_SSH_HOST` is set to read
  the bearer token out of a container.
- Remote MCP documentation describes self-hosting generically. Deployment-specific
  hostnames, application UUIDs, and host aliases no longer ship with the repo.

### Removed
- `docs/mcp-coolify-ops.md`, an operator runbook for one private deployment.

## [0.23.0] - 2026-07-30

### Added
- `sgw scrapingevals` exports ordinary provider-attempt traffic and allowlisted run evidence as the versioned `scrapingevals.sgw-observations/v1` staging contract.
- Stable database and event identities, replay-safe ledger cursors, private-target exclusion, URL redaction, and artifact hashes support incremental transfer without exposing response bodies or local paths.
- The ScrapingEvals integration guide assigns ownership from SGW's private ledger through receiver review and public promotion, while marking every passive run non-comparable and review-required.

## [0.22.1] - 2026-07-30

### Fixed
- The bundled agent skill (`docs/SKILL.md`) documented the tool as of v0.17.4 and omitted `sgw cost`, enforced `max_cost_per_url` budgets, domain-memory decay, selective evaluation, and the optional `--screenshot` destination. Its description now also matches on cost questions.

## [0.22.0] - 2026-07-29

### Added
- Opt-in `evaluation.mode: selective` applies the measured `selective-v1` runtime gate before an AI audit. Offline replay reaches 60/60 correct verdicts with 21 model calls instead of 60, saving 65% of calls while keeping good-page recall at 100%.
- `sgw calibrate-evaluator` reports the exact runtime predicate plus `off`, `audit`, and `selective` verdict, call, cost, and good-page-recall comparisons from committed responses.

### Changed
- `sgw url --screenshot [PATH]` accepts an optional output file and shows the saved screenshot path in the success result.
- `sgw run --screenshot [DIR]` accepts an optional existing directory and writes one input-ordered, URL-slugged image per captured result; bare batch requests list their telemetry artifact paths.

### Fixed
- Screenshot requests no longer silently capture nothing or lose an image when telemetry is disabled: the CLI either writes the explicit destination or reports clearly that no file was saved.

## [0.21.1] - 2026-07-29

### Fixed
- Calibration cost totals and per-judgment values use stable decimal precision, so the committed offline result contract replays identically on every supported Python version.

## [0.21.0] - 2026-07-29

### Added
- A versioned 60-case, zero-scrape-cost calibration corpus and `sgw calibrate-evaluator` command record live judgments once, then replay verdict, structured-label, human-review, cost, latency, and deterministic-comparison metrics offline without an API key.
- Committed baseline and held-out response evidence makes the selected evaluator's train/dev comparison, one-shot 24-case test result, and per-category free-check recommendation reproducible in CI.

### Changed
- The opt-in audit evaluator defaults to `google/gemini-3.5-flash-lite`, selected before holdout from train/dev evidence. Its `scrape-usability-v2` prompt is labeled `calibrated_v1_holdout_2026_07_29` only for that exact model/prompt pair; overrides remain uncalibrated.
- Audit status remains advisory: the selected model reached 100% TPR, 91.7% TNR, and 96.0% F1 on holdout, but `needs_human_review` missed the sole verdict error and must not be treated as a reliable escape hatch.
- Calibration reports count BYOK upstream inference cost when OpenRouter's billed cost is zero, avoiding false zero-cost model comparisons.

## [0.20.0] - 2026-07-28

### Added
- Default routing uses exact-profile attempt-ledger history to prefer the lowest sufficiently supported total spend per successful page, with lower weight for estimated costs and a deterministic daily probe for cheaper providers without recent usable evidence.
- `max_cost_per_url` now stops provider escalation before the current run ledger plus the next request-aware cost estimate would exceed the ceiling, with a distinct `budget_exceeded` result in Python, CLI, progress, logs, and telemetry.

### Changed
- ScrapeDrive enforces the remaining allowance before each internal 1-, 5-, or 25-unit tier, and Scrapfly receives a provider-side cost budget capped to the gateway remainder.
- Explicit request, recipe, and configured strategy routing continue to take precedence over observed-cost ordering; cold and thin histories retain provider cost-rank order.

## [0.19.0] - 2026-07-28

### Added
- Routing memory now has a configurable evidence window, defaulting to seven days, and sends expired failure streaks through a half-open recovery probe before the current learned winner.
- Provider adapters expose configuration availability, and missing or invalid credentials use a distinct `provider_unavailable` failure reason.

### Changed
- Provider preference and skip decisions derive from recent attempt-ledger rows for the exact domain, country, JavaScript, premium, mobile, and screenshot profile instead of monotonic domain counters.
- A learned winner moves to the front of the provider ladder without removing cheaper fallbacks.
- Known-unavailable providers are skipped before an attempt, and provider availability or proxy configuration failures do not count as domain routing evidence.
- Legacy `domain_provider_stats` and `domain_routes` data remains intact for compatibility and inspection, but the router no longer reads or updates those aggregate tables.

## [0.18.0] - 2026-07-28

### Added
- Every provider and adapter sub-attempt now contributes a structured run ledger with route, cost, exact-or-estimated provenance, latency, HTTP outcome, failure reason, and block type.
- The local SQLite memory store keeps append-only attempt rows with the full request profile, making historical cost, successful-provider, failed-spend, and credits-per-success queries possible without reading per-run JSON.
- `sgw cost` reports successful, failed, and total attempt spend by domain and provider over a configurable recent window, with Rich and JSON output.

### Changed
- `ScrapeResult.cost_units` now totals every internal sub-attempt made by that provider, including ScrapeDrive tier escalation and timeouts. `ScrapeResult.run_cost_units` totals the complete router fallback ledger while preserving the legacy single-cost fallback for callers that do not supply a ledger.
- Batch and telemetry totals derive from the complete run ledger instead of only the final provider result.
- Scrapfly marks provider-reported costs as exact and preserves known spend through failed API envelopes or CLOB retrievals; hardcoded adapter costs remain explicitly estimated.

## [0.17.7] - 2026-07-28

### Fixed
- MCP and development installs stay on the compatible v1 SDK until the server migrates to the renamed v2 `MCPServer` API, preventing fresh environments from failing to import `mcp.server.fastmcp`.

## [0.17.6] - 2026-07-28

### Fixed
- Block detection uses one shared signature source and length-gates ambiguous JavaScript, Akamai, login, consent, and generic-error phrases, preventing content-rich pages from being discarded while preserving high-precision challenge detection.

## [0.17.5] - 2026-07-28

### Fixed
- Cache show and purge commands accept only canonical artifact keys and enforce resolved-root containment, preventing relative, absolute, or symlinked targets from escaping the cache root.
- Run evidence validates caller-supplied IDs before routing and before every telemetry write. Invalid router metadata is replaced without failing the scrape, while direct recorder calls reject unsafe IDs.

## [0.17.4] - 2026-07-19

### Fixed
- Nodriver waits for the requested document readiness state before capturing HTML, selectors, or screenshots, preventing partial documents from being reported as complete.

## [0.17.3] - 2026-07-19

### Fixed
- Browser-backed providers leave navigation-only fetch metadata, resource Accept values, request priority, and user-agent selection to the browser engine so CSS and JavaScript subrequests are not rejected or fingerprinted inconsistently.
- Short generic error responses such as eBay's error page are rejected instead of being accepted as useful scrape results.

## [0.17.2] - 2026-07-19

### Fixed
- The Nodriver extension supports current Nodriver releases on Python 3.11 through 3.13 and rejects Python 3.14 before installation, where upstream generated protocol sources fail to parse.

## [0.17.1] - 2026-07-19

### Fixed
- Camoufox disables Playwright's default viewport command so its Firefox protocol remains compatible with Playwright 1.61.
- Nodriver excludes the malformed 0.50.3 release and captures screenshots through the supported filename-based API.

## [0.17.0] - 2026-07-18

### Added
- Opt-in Requests, Botasaurus, Playwright, Pydoll, Helium, and Scrapy extensions turn the deterministic engines from the Zenbook ScrapingEvals lab into Gateway providers.
- The `sg-cdp` extension registers Chrome CDP and Lightpanda providers for rendered HTML from externally managed browser endpoints, with PNG screenshots on Chrome.

### Changed
- Scrapy requests run in isolated child processes so Twisted reactor state cannot leak into long-running CLI, REST, or MCP processes.
- The source registry and provider documentation map thirteen available local-engine packages back to the ScrapingEvals tool catalog and distinguish agent workflows from deterministic URL providers.

## [0.16.0] - 2026-07-18

### Added
- Scrapfly, Firecrawl, Jina Reader, ZenRows, Oxylabs, Bright Data Web Unlocker, and Spider Cloud expand the hosted API routing catalog with provider-native options and response handling.
- Crawl4AI adds a self-hosted Docker API route for rendered HTML, native Markdown, selectors, mobile viewports, and screenshots.
- Opt-in Scrapling, Camoufox, SeleniumBase CDP Mode, Patchright, Nodriver, and Crawlee extensions keep local browser engines out of the default installation.
- A staged spider-rs extension defines the provider contract while its upstream Linux package remains unbuildable.

### Changed
- The extension registry exposes source-installable Browserless and local-engine packages directly from this repository.
- Provider documentation now covers all fifteen built-ins and the isolated local-engine installation model.

## [0.15.0] - 2026-07-18

### Added
- Domain recipe YAML files can define provider order, request settings, validation phrases, known failure content, and cache freshness for matching sites and subdomains.
- `sgw serve` exposes FastAPI endpoints for scraping, cache artifacts, learned domain statistics, health checks, and interactive OpenAPI documentation.
- A production-ready Docker Compose definition provides a persistent data volume, health check, environment template, and a direct Coolify import path.

### Changed
- The persistent MCP and browser-console process also serves the token-protected REST API under `/v1` and a public `/health` endpoint.
- Domain memory supports gateways constructed outside the ASGI worker thread while keeping operations synchronous and transactional.

## [0.14.0] - 2026-07-18

### Added
- `sgw meta` and `sgw url --meta` now extract Twitter Card tags, JSON-LD blocks, canonical and icon URLs, charset, and robots directives alongside existing OpenGraph keys.

### Changed
- Relative canonical and icon URLs resolve against the scraped page, while malformed JSON-LD blocks are skipped without hiding other valid metadata.

## [0.13.1] - 2026-07-18

### Fixed
- Provider hit-rate summaries now include only names from the resolved provider registry, including extension providers, and report omitted non-provider attempt records separately.
- Pytest redirects relative run, evaluation, memory, cache, and log paths into its temporary directory instead of polluting an operator's local `.scrape-gateway` history.

## [0.13.0] - 2026-07-18

### Added
- `sgw url` and `sgw run` can write the selected HTML or Markdown content to an existing directory with `--output`/`-o`, while Rich status stays in the console.
- `sgw telemetry --summary` aggregates domain success rates, common diagnoses, average attempts and cost, and provider wins per attempt over the selected recent reports, with optional JSON output.
- The README compares all eight current adapters by JavaScript, screenshot, native Markdown, country, CAPTCHA, and routing cost capability, with a regression test against provider declarations.

## [0.12.0] - 2026-07-17

### Added
- Completed traces can be retried with their original capture options, a guaranteed cache bypass, and an optional per-run provider override while the replacement run stays in focus.
- New console scrapes can prefer any currently enabled provider for that run without changing shared routing settings, and completed traces identify the override in their header.
- Run and tab selections now live in shareable `?run=` and `?tab=` links that survive authentication, include a one-click copy action, and explain unknown or expired run IDs.
- Every completed trace starts with a plain-language outcome and recommended next step, with accessible explanations for audit, validator, cache, review, and provider-server-error terminology.

## [0.11.0] - 2026-07-17

### Added
- Active traces now show the current routing, provider, evaluation, or persistence activity with a client-side elapsed clock between telemetry polls.
- Responsive trace-loading skeletons, newly appended step feedback, and subtle running and hover motion make state changes visible without disrupting inspection.

### Changed
- A scrape that completes while still watched resolves in place to its own persisted trace and announces the outcome; navigating elsewhere during the run preserves the operator's selection.

### Fixed
- Active polling now begins immediately at the one-second cadence, and the trace panel keeps the remaining workspace height instead of leaving the tab strip above an empty void.
- Stale inventory or detail responses can no longer replace a newer trace selection during the active-to-completed handoff.

## [0.10.5] - 2026-07-17

### Fixed
- Rendered consent walls up to 8 KiB remain blocked while full pages above that evidence-backed boundary still avoid cookie-language false positives.

## [0.10.4] - 2026-07-17

### Fixed
- Consent-wall signatures now apply only to responses shorter than 8,000 characters, so cookie documentation and pages with non-blocking preference controls pass validation.

## [0.10.3] - 2026-07-17

### Fixed
- ScrapeDrive tier escalation and screenshot retrieval now share one configured provider timeout instead of resetting the budget for every tier.

## [0.10.2] - 2026-07-17

### Fixed
- Clean development installs now include the MCP and web runtime required by the committed server test suite.

## [0.10.1] - 2026-07-17

### Fixed
- Repository lint and formatting drift no longer stops GitHub Actions before the test suite can run.

## [0.10.0] - 2026-07-17

### Added
- Active console runs now expose provider, validation, screenshot, AI-evaluation, and persistence progress before the final telemetry report exists, with one-second polling and refresh recovery.
- An authenticated gateway settings dialog can globally enable or disable providers and configure default, per-provider, and AI-evaluation timeouts for subsequent console and MCP runs.
- A dedicated Visual view renders authenticated screenshot artifacts and clearly distinguishes not-requested, in-progress, captured, and requested-but-missing states.
- Final HTML, Markdown, and screenshot evidence is now saved for every telemetry run independently of whether AI evaluation is enabled.

### Changed
- Console-owned routing settings persist locally in `.scrape-gateway/operator-settings.yml`, override the base YAML without rewriting it, and reload the shared gateway used by MCP tools.
- ScrapeDrive now honors the configured provider timeout and downloads returned screenshot URLs into the scrape result.

### Fixed
- A screenshot-required ScrapeDrive attempt can no longer report success when it returned no usable image evidence.

## [0.9.2] - 2026-07-16

### Fixed
- In-flight console scrapes are now tracked by the service, remain running when the initiating browser request disconnects, and reappear with the same active ID after a page refresh.
- Live refresh now follows active work until its persisted trace is available while preserving an operator's selection when they inspect an older run.

## [0.9.1] - 2026-07-16

### Fixed
- Console CSS and JavaScript references now carry a content fingerprint, preventing a new trace shell from loading stale Cloudflare-cached assets from an earlier release.
- The console shell now revalidates on each visit so deployments become visible without waiting for the edge-cache TTL.

## [0.9.0] - 2026-07-16

### Added
- An observability-style trace explorer with a live run inventory, ordered lifecycle timeline, recorded-duration waterfall, selectable step attributes, and dedicated output, AI evaluation, artifact, and raw-report views.
- Normalized trace data on run-detail API responses for request, cache, provider, validation, transformation, evaluation, result, and persistence steps, including explicit recorded-versus-order-only timing semantics.
- Compact in-flight feedback while a console scrape is running and an optional 15-second live refresh for saved traces.

### Changed
- Scrape controls now open in a conventional responsive dialog so trace history and the active inspection surface remain the primary workspace.

### Fixed
- Browserless credentials now use bearer headers instead of query parameters, preventing ordinary HTTP request logs from persisting the token.

## [0.8.1] - 2026-07-16

### Fixed
- The combined HTTP service now keeps FastMCP's authentication middleware, so valid bearer tokens can use `/mcp` while the browser console is enabled.

## [0.8.0] - 2026-07-16

### Added
- A browser console at `/` for starting scrapes and reviewing saved runs, AI checks, provider attempts, recurring improvements, usage, and costs.
- Protected HTTP APIs for run history, evaluation summaries, scrape submission, and safe access to saved Markdown, HTML source, JSON, and screenshot artifacts.
- The console uses the existing MCP bearer token and keeps it only in browser `sessionStorage`.

### Changed
- The HTTP process now serves the browser console and `/mcp` from one lifespan managed Starlette application.
- Browser and audit views use the same 500 run window, so every summary result can be found with the filters.

## [0.7.2] - 2026-07-16

### Fixed
- Continuous-improvement summaries now discard verbose no-op suggestions such as “None required as…” and combine page-type labels that differ only by capitalization or separators.

## [0.7.1] - 2026-07-16

### Fixed
- OpenRouter audits now reuse complete provider, BYOK, and upstream-cost details from the completion response, avoiding redundant generation-detail retries and their eventual-consistency 404s.

## [0.7.0] - 2026-07-16

### Added
- Optional, non-blocking OpenRouter scrape-usability audits with `google/gemini-3.1-flash-lite`, a strict binary verdict, categorical diagnostic checks, task-specific goals, and Markdown plus screenshot evidence.
- Complete per-run evaluation bundles containing the evaluator request/response, final HTML and Markdown, screenshots, hashes, usage, costs, provider details, and failed-provider artifacts.
- `sgw evaluations` aggregation with failed checks, page types, root causes, actionable improvements, usage totals, and a manual review queue.
- CLI and MCP support for evaluation goals, screenshot evidence, and returning audit results and report pointers.

### Changed
- Browserless screenshot requests now fetch rendered HTML and the screenshot concurrently so validation and audits keep both evidence types.
- Cached results restore coherent HTML, Markdown, and screenshot artifacts, preserving visual evidence without repeated provider calls.
- Telemetry redaction recognizes nested credential-key variants before request context is persisted.

### Fixed
- Unquoted YAML `evaluation.mode: off` is accepted despite PyYAML parsing it as boolean false.
- No-op evaluator phrases such as “no improvements needed” no longer pollute aggregate improvement suggestions.
- 202 unit tests (up from 194 before this release).

## [0.6.0] - 2026-07-05

### Added
- `sg-browserless` provider extension for Browserless rendered HTML and screenshots
- MCP Docker image now bundles `sg-browserless`, so hosted MCP deployments can route JS rendering and screenshot requests through Browserless.

## [0.5.0] - 2026-05-30

### Added
- `skip_validation` field on `ScrapeRequest` — skip content validation for non-HTML resources (robots.txt, sitemap XML)
- `sg-sitemap` now fetches through the scrape gateway pipeline (anti-bot bypass, proxies, provider fallback)
- `sgw sitemap --provider` and `--no-cache` flags

### Changed
- `sg-sitemap` no longer depends on trafilatura — uses stdlib XML parsing

## [0.4.0] - 2026-05-29

### Added
- CLI command extension loading via `scrape_gateway.commands` package entry points
- Local command extensions from `~/.config/scrape-gateway/commands/`
- Example `sg-sitemap` extension that adds `sgw sitemap` using Trafilatura
- `sgw extensions` registry output now shows extension type and installed command extensions
- 169 unit tests (up from 164)

## [0.3.0] - 2026-05-27

### Added
- Auto-spoof Referer header on every scrape (Google search URL by default)
- `--referer` CLI flag on `sgw url` and `sgw run`
- `referer` field on `ScrapeRequest` (None=auto, string=custom, ""=disabled)
- 164 unit tests (up from 160)

## [0.2.0] - 2026-05-27

### Added
- `sgw meta` command — extract OpenGraph metadata as JSON
- `sgw telemetry` command — inspect recent scrape reports with filters
- Telemetry system: JSON report per scrape run with diagnosis and recommended next action
- `--tier` flag on `sgw url` / `sgw run` to force ScrapeDrive tier
- `--meta` flag on `sgw url` for inline OG metadata extraction
- `--debug-artifacts` flag to save failed response bodies for analysis
- `PROXY_ERROR` failure reason with `classify_exception()` for exception-based detection
- Validators now capture `matched_pattern` and surrounding `snippet` for evidence

### Fixed
- Proxy misconfiguration no longer burns through the entire provider chain — router stops on PROXY_ERROR
- All HTTP providers (raw, curl_cffi, wreq) retry direct when proxy fails
- Cache keys now include `render_js` so static and JS-rendered pages don't collide
- Login-wall detection only fires on short pages (< 8KB) to avoid false positives on forums
- Dotenv loader no longer overrides env vars already set in the shell
- `sgw url` exits non-zero on failure for shell pipeline use
- `failure_reason` prints value not repr in CLI output

### Changed
- Block signatures for login walls tightened ("create an account" removed, length gate added)
- 160 unit tests (up from 136)

## [0.1.0] - 2026-05-09

### Added
- Core CLI: `sgw url`, `sgw extract`, `sgw recipe`, `sgw detect`, `sgw links`, `sgw follow`
- 7 providers: raw_http, wreq, curl_cffi, scrapedrive, scrape_do, scrapingbee, scraperapi
- Cheapest-first routing with domain memory and content validation
- Extension system: entry points + local `~/.config/scrape-gateway/providers/` directory
- `sgw providers` — list all discovered providers
- `sgw extensions` — browse/install from curated registry
- `sgw setup` — interactive provider configuration wizard
- Auto-install prompt for extension dependencies
- LLM-assisted pattern picking in `sgw extract`
- YAML recipe workflows for repeatable multi-URL jobs
- Cache layer with configurable TTL
- 136 unit tests + 8 ScraperAPI live tests + ScrapeDrive live tests
- Claude Code skill (`docs/SKILL.md`)
