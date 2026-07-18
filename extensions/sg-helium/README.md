# sg-helium

Scrape Gateway provider for Helium's Selenium-based Chrome automation.

```bash
sgw extensions sg-helium
sgw url https://example.com --render-js --screenshot -p helium
```

Chrome or Chromium must be available on the host. The complete blocking browser
session runs in a worker thread.
