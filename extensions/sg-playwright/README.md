# sg-playwright

Direct Playwright Chromium rendering for Scrape Gateway.

```bash
sgw extensions sg-playwright
python -m playwright install chromium
sgw url https://example.com --render-js --screenshot -p playwright
```

The provider supports rendered HTML, response status, selector/event waits,
mobile context emulation, and full-page PNG screenshots.
