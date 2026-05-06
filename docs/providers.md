# Provider notes

## Scrape.do

Uses `https://api.scrape.do/` with `token` and `url`. Current adapter maps:

- `country` → `geoCode`
- `premium` → `super=true`
- `render_js` → `render=true` placeholder; verify exact headless-browser parameter for your plan

## ScrapingBee

Uses `https://app.scrapingbee.com/api/v1/`. Current adapter maps:

- `render_js` → `render_js`
- `premium` → `premium_proxy=true`
- `country` → `country_code`

## ScraperAPI

Uses `https://api.scraperapi.com/`. Current adapter maps:

- `render_js` → `render=true`
- `premium` → `premium=true`
- `country` → `country_code`
- `screenshot` → `screenshot=true`

## ScrapeDrive

The adapter is intentionally isolated and configurable:

- `SCRAPEDRIVE_API_KEY`
- `SCRAPEDRIVE_BASE_URL`

Verify endpoint, auth scheme, request body, and response shape against `https://scrapedrive.com/docs/` before publishing.
