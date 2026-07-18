# Domain recipes

Put one YAML file per domain in this directory. A matching recipe can put known-good
providers first, set request options, tighten content validation, and override cache
freshness for that domain.

```yaml
domain: shop.example

routes:
  - provider: scrapedrive
    settings:
      country_code: US
      render_js: true
      premium: true
      scrape_tier: advanced
  - provider: browserless

validators:
  min_text_chars: 500
  must_contain_any: [reviews, pricing]
  must_not_contain: [product not found]

failure_patterns:
  blocked: [temporarily unavailable, access denied]

ttl: 14d
```

Matching includes subdomains, and the most specific matching domain wins. Explicit
per-request provider selection still has the highest routing priority. Otherwise the
order is domain recipe, configured strategy, learned domain memory, then provider cost.

The first route's settings become request defaults before cache lookup and provider
selection. Supported settings are `country` or `country_code`, `render_js`, `premium`,
`screenshot`, `mobile`, `wait_event`, `wait_selector`, `extra_wait_ms`, `block_ads`,
`output_format`, `timeout_seconds`, `referer`, `skip_validation`, and `scrape_tier`.

`failure_patterns` groups are labels for operators; every listed phrase is treated as
forbidden content. TTL accepts seconds or the same `s`, `m`, `h`, and `d` suffixes as
the global cache configuration.
