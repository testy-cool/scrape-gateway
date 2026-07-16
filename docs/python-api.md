# Python API

## Basic usage

```python
import asyncio
from scrape_gateway import ScrapeGateway, ScrapeRequest

async def main():
    gateway = ScrapeGateway.from_config()
    result = await gateway.scrape(ScrapeRequest(
        "https://example.com",
        country="us",
        render_js=False,
    ))
    print(result.provider, result.success, result.route)
    print(result.html[:500])

asyncio.run(main())
```

## ScrapeRequest fields

| Field | Type | Default | Description |
|---|---|---|---|
| `url` | str | required | URL to scrape |
| `country` | str | None | Geo-target (e.g. "us", "gb") |
| `render_js` | bool | False | Render JavaScript before returning |
| `premium` | bool | False | Use highest-tier provider options |
| `mobile` | bool | False | Mobile user agent |
| `timeout_seconds` | int | 30 | Request timeout |
| `wait_event` | str | None | Wait for JS event (e.g. "networkidle") |
| `wait_selector` | str | None | Wait for CSS selector to appear |
| `output_format` | str | "html" | "html" or "markdown" |
| `screenshot` | bool | False | Request image evidence from a capable provider |
| `metadata` | dict | {} | Pass-through metadata (e.g. `{"evaluation_goal": "Capture visible products"}`) |

## ScrapeResult fields

| Field | Type | Description |
|---|---|---|
| `url` | str | The scraped URL |
| `provider` | str | Which provider succeeded (or last one tried) |
| `success` | bool | Whether the scrape succeeded |
| `status_code` | int | HTTP status code |
| `html` | str | Raw HTML content |
| `markdown` | str | Markdown conversion |
| `error` | str | Error message on failure |
| `failure_reason` | FailureReason | Classified failure type |
| `cost_units` | float | Relative cost of this scrape |
| `route` | str | Provider chain path taken |
| `metadata` | dict | Run ID, telemetry report path, etc. |
