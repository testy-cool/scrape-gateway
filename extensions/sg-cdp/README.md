# sg-cdp

Scrape Gateway providers for browsers that expose the Chrome DevTools Protocol.
The package registers two provider names:

- `chrome_cdp`, configured with `CHROME_CDP_URL`
- `lightpanda`, configured with `LIGHTPANDA_CDP_URL`

```bash
sgw extensions sg-cdp
export CHROME_CDP_URL=http://127.0.0.1:9222
sgw url https://example.com --render-js --screenshot -p chrome_cdp
```

The endpoint can be an HTTP discovery URL or a browser WebSocket URL. The
adapter opens and closes its own page, then disconnects from the remote browser.
