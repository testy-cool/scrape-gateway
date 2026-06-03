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

- `POST /content?token=...` for rendered HTML
- `POST /screenshot?token=...` for screenshots
