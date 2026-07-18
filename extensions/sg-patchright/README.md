# sg-patchright

Adds Patchright's patched Playwright Chromium runtime to `sgw`.

```bash
uv pip install -e . -e extensions/sg-patchright
uv run patchright install chromium
sgw url https://example.com -p patchright --render-js --screenshot
```

The provider uses the upstream async Playwright-compatible API and captures full-page PNGs.
