# Writing extensions

The 7 built-in providers cover most scraping needs, but you can add your own — an Amazon Product API, a Wayback Machine fetcher, a headless browser, anything that takes a URL and returns content.

Three ways to add providers, in order of effort:

## 1. Drop a file (easiest)

Put a `.py` file in `~/.config/scrape-gateway/providers/`:

```python
from scrape_gateway import ProviderAdapter, ScrapeRequest, ScrapeResult

class MyProvider(ProviderAdapter):
    name = "my_api"
    cost_rank = 10
    capabilities = frozenset({"html"})
    install_requires = ["some-package"]  # auto-installed on first use

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        # your logic here
        ...
```

`sgw` discovers it automatically. Run `sgw providers` to verify.

If your provider needs a pip package, set `install_requires` — `sgw` will prompt to install it the first time it loads.

## 2. Install from the registry

```bash
sgw extensions                    # browse available extensions
sgw extensions sg-playwright      # install one into sgw's own venv
```

## 3. Publish a pip package

Create a package that declares an entry point:

```toml
[project.entry-points."scrape_gateway.providers"]
my_provider = "my_package:MyProvider"
```

After `pip install my-package`, `sgw` discovers it automatically.

See `examples/extension_example.py` for a complete template.
