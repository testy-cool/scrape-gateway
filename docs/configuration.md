# Configuration

`sgw` works with zero configuration. The YAML file and `.env` are for overriding defaults.

## API keys (`.env`)

```bash
SCRAPEDRIVE_API_KEY=your_key_here
SCRAPE_DO_TOKEN=your_token_here
SCRAPINGBEE_API_KEY=your_key_here
SCRAPERAPI_API_KEY=your_key_here
```

Without paid API keys, `sgw` uses free providers only (raw_http, wreq, curl_cffi).

Optional proxy:
```bash
SCRAPE_PROXY_URL=http://user:pass@proxy.example.com:8080
```

## YAML config (`scrape-gateway.yml`)

Place in project root or CWD. All sections are optional.

```yaml
cache:
  ttl: 24h
  root: .scrape-gateway/artifacts

providers:
  - raw_http
  - wreq
  - curl_cffi
  - name: scrapedrive
    enabled: true
  - name: scraperapi
    enabled: false

strategy:
  mode: cheapest_successful
  provider: scrapedrive       # override: always try this first

telemetry:
  enabled: true
  root: .scrape-gateway/runs
  debug_artifacts: false
```

## TTL format

Cache TTL accepts human-friendly strings: `30s`, `5m`, `24h`, `7d`, or a raw number (seconds).
