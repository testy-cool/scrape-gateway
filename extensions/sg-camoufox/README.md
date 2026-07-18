# sg-camoufox

Adds Camoufox rendered HTML and screenshots to `sgw`.

```bash
uv pip install -e . -e extensions/sg-camoufox
uv run python -m camoufox fetch
sgw url https://example.com -p camoufox --render-js --screenshot
```

Camoufox is experimental upstream. Keep it isolated to workloads that need its
Firefox fingerprint rotation and verify target-site behavior after upgrades.
