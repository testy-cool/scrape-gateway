# sg-crawlee

Adds a one-request Crawlee Playwright crawler to `sgw`.

```bash
uv pip install -e . -e extensions/sg-crawlee
uv run playwright install chromium
sgw url https://example.com -p crawlee --render-js --screenshot
```

Each gateway call creates a bounded crawler with `max_requests_per_crawl=1`, so
links discovered on the target page are not enqueued implicitly.
