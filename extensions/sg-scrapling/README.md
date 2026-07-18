# sg-scrapling

Adds Scrapling's fast HTTP fetcher and stealth Patchright browser to `sgw`.

```bash
uv pip install -e . -e extensions/sg-scrapling
uv run scrapling install
sgw url https://example.com -p scrapling
sgw url https://example.com -p scrapling --render-js
```

Plain requests use `AsyncFetcher`; `--render-js` or `--premium` selects
`StealthyFetcher`. Browser dependencies are installed only when this extension is chosen.
