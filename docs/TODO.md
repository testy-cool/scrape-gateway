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

## Known gaps (2026-07-29)

- [x] **Adopt ruff 0.16's default rules.** Done 2026-08-04, pin lifted to `ruff>=0.16`.
  The estimate here was stale: it was 103 findings by the time anyone ran it, not ~57,
  and 61 were auto-fixable rather than 21. 102 were mechanical or configuration. The
  substantive ones were a shared mutable class default on `ProviderAdapter`
  (fixed separately) and two `SIM103` rewrites that made the code worse and were
  reverted behind a `# noqa` with the reason. `TRY004` and `B008` are switched off with
  documented rationale rather than followed.
- [x] **`estimated_cost_units` defaults to `0.0`.** Fixed 2026-08-04. The base now returns
  infinity unless the adapter sets `is_free = True`, and the router treats an unpriced
  provider as unaffordable while a ceiling is set. Note the original claim that "every
  in-tree adapter overrides it" was wrong: five did not (`raw_http`, `wreq`, `curl_cffi`,
  `crawl4ai`, `jina_reader`), and they are now marked `is_free`. A regression test walks
  the providers package so a new adapter cannot inherit the unpriced default unnoticed.
- [ ] **Evaluator `needs_human_review` is unreliable.** Measured at 0 of 1 held-out
  verdict errors and 0 of 2 train/dev errors — it does not fire on the model's own
  mistakes. Do not build gating on it until it is fixed or removed. See
  `docs/evaluator-calibration-v1.md`.
- [ ] **Calibration corpus is small.** 60 cases, 24 held out. The bootstrap 95% CI on
  held-out specificity is [0.73, 1.00]. Grow the corpus before treating the specificity
  number as precise.
