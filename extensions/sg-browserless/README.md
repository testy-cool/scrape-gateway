# sg-browserless

`sg-browserless` adds a Browserless provider to scrape-gateway.

Use it when you want JavaScript-rendered HTML or screenshots from your own
Browserless service instead of a scraping API provider.

## Install locally

From the scrape-gateway repo:

```bash
uv pip install -e . -e extensions/sg-browserless
```

If `sgw` is already installed in the target environment:

```bash
uv pip install -e extensions/sg-browserless
```

## Configure

Set these in `.env` or the shell environment:

```bash
BROWSERLESS_URL=https://browserless.example.com
BROWSERLESS_TOKEN=your_token_here
```

## Usage

```bash
sgw providers
sgw url https://example.com --render-js -p browserless
sgw url https://example.com --render-js --screenshot -p browserless
```

The provider uses Browserless REST endpoints:

- `POST /content` for rendered HTML
- `POST /screenshot` for screenshots

Both requests send `BROWSERLESS_TOKEN` in the `Authorization: Bearer` header so
the credential does not appear in request URLs or HTTP client logs.

Screenshot requests call both endpoints concurrently and return rendered HTML and image
evidence for the same URL and render settings instead of an image with no extractable
body. Browserless handles them as separate requests, so highly dynamic pages can still
change slightly between the two captures.
