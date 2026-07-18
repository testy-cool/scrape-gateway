# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
