# sg-spider-rs

Adds the Rust-backed `spider-rs` single-page fetcher to `sgw`.

The adapter intentionally fetches one page and does not turn a gateway request into a
site-wide crawl.

The package is staged but not currently registry-installable on Linux. PyPI 0.0.57 has
invalid package metadata, while the metadata-fixed v0.0.58 Git tag fails to compile after
resolving an incompatible current `spider` crate API. The registry keeps this extension
planned until upstream publishes a buildable Linux package or pins compatible Rust
dependencies.

After that upstream blocker is fixed, the intended local workflow is:

```bash
uv pip install -e . -e extensions/sg-spider-rs
sgw url https://example.com -p spider_rs
```
