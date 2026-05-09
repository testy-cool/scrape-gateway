# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
