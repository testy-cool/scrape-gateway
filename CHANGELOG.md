# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
