# Provider wire contracts

These versioned JSON files are the source of truth for facts supplied by a scraping provider:
endpoint, authentication transport, request shapes, target-response metadata, error mapping, and
cost units. Python adapters and prose remain handwritten, but tests must prove they agree with the
contract.

To add a contract:

1. Copy `v1/template.json` to `v1/<provider>.json`.
2. Replace every example value with facts from current official provider documentation.
3. Keep the filename and top-level `provider` value identical to the built-in entry-point name.
4. Run `make contract-check`.
5. Follow the remaining adapter, discovery, documentation, and release surfaces in
   `docs/references/adding-built-in-provider.md`.

`schema.json` and `template.json` are metadata, not provider contracts. The validator discovers
every other `v1/*.json` file automatically and rejects schema errors, duplicate providers,
filename mismatches, or contracts without a registered built-in adapter.

ScrapingEvals (`sev`) vendors only the contracts it consumes and pins their upstream revision and
hashes. See `docs/references/sev-engine-integration.md`; do not edit SEV's snapshot from this
repository.
