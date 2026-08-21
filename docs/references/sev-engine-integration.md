# Scrape Gateway and SEV provider integration

Scrape Gateway and ScrapingEvals CLI (`sev`) are separate products and repositories:

- SGW: `https://github.com/testy-cool/scrape-gateway`
- SEV: `https://github.com/testy-cool/scrapingevals-cli`

Adding an SGW adapter does not add a SEV engine, copy credentials, update a dashboard, deploy a
hub, or prove an installed SEV checkout. When a task names both products, implement and verify each
repository independently.

## Source-of-truth boundary

SGW owns provider wire facts under `src/scrape_gateway/provider_contracts/`: endpoints,
authentication transport, request mappings, response metadata, provider errors, and billed or
estimated units.

SEV owns its engine registry and behavior: engine slug and aliases, default HTML/render tracks,
credential discovery, concurrency limits, CLI ordering, Gemini judgement, hub/dashboard display,
artifact presentation, and deployment configuration.

SEV vendors consumed SGW files under `src/scrapingevals_cli/provider_contracts/`. Its `lock.json`
records the SGW repository, source revision, file list, and SHA-256 hashes, so SEV has no runtime
dependency on an SGW checkout.

## Snapshot and parity flow

1. Merge and push the SGW contract and adapter with `make contract-check` green.
2. In a clean SEV checkout, update the snapshot from an explicit local SGW checkout:

   ```bash
   uv run python scripts/sync_provider_contracts.py \
     --update --source /path/to/scrape-gateway
   ```

3. Inspect and commit only the intended vendored files and `lock.json` in SEV.
4. Run SEV's provider-contract and engine-registry tests. The check form is:

   ```bash
   uv run python scripts/sync_provider_contracts.py \
     --check --source /path/to/scrape-gateway
   ```

5. Release and deploy SEV separately. Where a hub exists, `sev doctor --hub --json` must report
   matching revision, registry, and contract digests before parity is claimed.

The snapshot command intentionally requires an explicit checkout path. Public SGW documentation
must not include private workstation paths, deployment hosts, or credentials.

## Not the passive feed

The SEV engine integration above is distinct from `sgw scrapingevals`. That command exports a
privacy-safe operational-observation feed to the separate ScrapingEvals evidence repository and
does not create or configure a `sev` engine. See [`../scrapingevals-feed.md`](../scrapingevals-feed.md)
for that contract.
