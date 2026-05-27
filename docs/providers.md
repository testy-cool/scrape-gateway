# Providers

7 built-in, 3 free. The router tries free providers first, then escalates to paid.

| Provider | Cost Rank | Free | JS | Geo | Anti-bot | Notes |
|---|---|---|---|---|---|---|
| `raw_http` | 0 | yes | no | no | none | Plain HTTP GET via httpx |
| `wreq` | 2 | yes | no | no | TLS fingerprinting | Impersonates real browser TLS |
| `curl_cffi` | 3 | yes | no | no | TLS fingerprinting | Similar to wreq, different engine |
| `scrapedrive` | 25 | no | yes | yes | full | 3 tiers: standard / advanced / hyperdrive |
| `scrape_do` | 30 | no | yes | yes | residential proxies | |
| `scrapingbee` | 35 | no | yes | yes | premium proxies | |
| `scraperapi` | 40 | no | yes | yes | premium proxies | Supports screenshots |

## API parameter mapping

Each provider has its own API conventions. The adapter layer translates `sgw`'s common interface.

### ScrapeDrive

- Endpoint: configurable via `SCRAPEDRIVE_BASE_URL`
- Auth: `SCRAPEDRIVE_API_KEY`
- Tiers: standard → advanced → hyperdrive (auto-escalates on failure)
- `premium` flag maps to hyperdrive tier
- `country` auto-upgrades to advanced tier (geo-proxies required)

### Scrape.do

- Endpoint: `https://api.scrape.do/`
- Auth: `SCRAPE_DO_TOKEN` (passed as `token`)
- `country` → `geoCode`
- `premium` → `super=true`
- `render_js` → `render=true`

### ScrapingBee

- Endpoint: `https://app.scrapingbee.com/api/v1/`
- Auth: `SCRAPINGBEE_API_KEY` (passed as `api_key`)
- `render_js` → `render_js`
- `premium` → `premium_proxy=true`
- `country` → `country_code`

### ScraperAPI

- Endpoint: `https://api.scraperapi.com/`
- Auth: `SCRAPERAPI_API_KEY` (passed as `api_key`)
- `render_js` → `render=true`
- `premium` → `premium=true`
- `country` → `country_code`
- `screenshot` → `screenshot=true`
