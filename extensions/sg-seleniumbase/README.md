# sg-seleniumbase

Adds SeleniumBase's undetected async CDP Mode HTML and screenshots to `sgw`.

```bash
uv pip install -e . -e extensions/sg-seleniumbase
sgw url https://example.com -p seleniumbase --render-js --screenshot
```

The adapter uses SeleniumBase's async CDP driver instead of WebDriver. This avoids
the UC WebDriver startup path while retaining SeleniumBase's direct Chrome control
and server-compatible headless execution.
