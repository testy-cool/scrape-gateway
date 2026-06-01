# sg-cache

`sg-cache` adds cache inspection commands to scrape-gateway.

It is useful for long-lived MCP deployments where `.scrape-gateway/` persists
across restarts and redeploys.

## Install locally

From the scrape-gateway repo:

```bash
uv pip install -e . -e extensions/sg-cache
```

If `sgw` is already installed in the target environment:

```bash
uv pip install -e extensions/sg-cache
```

## Usage

```bash
sgw cache stats
sgw cache ls
sgw cache ls --domain example.com --format json
sgw cache show https://example.com
sgw cache show <cache-key> --format meta
sgw cache purge https://example.com
sgw cache purge --domain example.com --yes
sgw cache purge --expired --yes
```

By default it reads the cache root from `scrape-gateway.yml`. If no config is
present, it uses `.scrape-gateway/artifacts`, matching scrape-gateway defaults.
