# sg-sitemap

`sg-sitemap` adds a `sgw sitemap` command to scrape-gateway. It uses Trafilatura's sitemap discovery/parsing utilities to find page URLs from a site's sitemap files.

## Install locally

From the scrape-gateway repo, install both the local `sgw` package and the extension into the same environment:

```bash
uv pip install -e . -e extensions/sg-sitemap
```

If `sgw` is already installed in the target environment, installing only the extension is enough:

```bash
uv pip install -e extensions/sg-sitemap
# or: pip install -e extensions/sg-sitemap
```

## Usage

```bash
sgw sitemap https://example.com
sgw sitemap https://example.com -f txt
sgw sitemap https://example.com -f rich --limit 50
sgw sitemap https://example.com --lang en
sgw sitemap https://example.com --discover-only
```

Formats:

- `json` (default): machine-readable object with `count` and `urls`
- `txt`: one URL per line
- `rich`: terminal table

`--discover-only` only reports sitemap URLs declared in `robots.txt`; the default command expands the sitemap(s) and prints page URLs.
