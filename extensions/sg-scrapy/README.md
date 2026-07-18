# sg-scrapy

Scrape Gateway provider for bounded, single-page Scrapy fetches.

```bash
sgw extensions sg-scrapy
sgw url https://example.com -p scrapy
```

Every request runs in a short-lived worker process. This prevents Twisted's
non-restartable reactor from contaminating Scrape Gateway's long-running async
server.
