# TODO

## Metadata extraction improvements

`sgw meta` now extracts social, structured, and document metadata. Remaining additions:

- [x] Twitter Card tags (`twitter:title`, `twitter:image`, etc.)
- [x] JSON-LD structured data (schema.org)
- [x] Canonical URL (`<link rel="canonical">`)
- [x] Favicon / apple-touch-icon URLs
- [x] charset detection
- [ ] language detection
- [x] `<meta name="robots">` directives
- [ ] Structured output on `ScrapeResult.metadata` (not just CLI print)
