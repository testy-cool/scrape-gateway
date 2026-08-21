# Adding a built-in provider

Use this fast path for a provider that belongs in the `sgw` routing ladder. Use an
extension instead when the provider needs optional heavyweight dependencies or should
ship independently.

## Boundary first

An `sgw` provider and a ScrapingEvals (`sev`) engine are separate integrations. Adding
an adapter here does not create a row in `sev`, copy credentials to its host, or change
its Web UI. If the request names both products, finish and verify each repository
separately.

## Establish the wire contract once

Before editing, obtain current official documentation for:

- endpoint and HTTP method;
- authentication and the environment-variable name;
- plain HTML versus JavaScript-rendered request parameters;
- country and premium/residential routing;
- target status, final URL, and content shape;
- timeouts, waits, screenshots, and caller headers when supported;
- rate-limit, auth, and provider-error responses;
- billed units for each request shape and any exact cost response field/header.

Never infer costs or provider model IDs. Keep the real key only in ignored `.env`; add
an empty variable to `.env.example`.

Record those facts once in
`src/scrape_gateway/provider_contracts/v1/<name>.json`, validated by the adjacent
`schema.json`. This file is the source of truth for provider wire semantics; adapters
and prose remain handwritten and are tested against it. SEV vendors a pinned snapshot
of contracts it consumes, so changing a contract does not create a runtime dependency
between the two tools.

## Complete surface map

Touch these files in one pass; do not rediscover the list by broad searching:

| Surface | Required change |
|---|---|
| `src/scrape_gateway/providers/<name>.py` | Adapter and request/response mapping |
| `pyproject.toml` | `scrape_gateway.providers` entry point |
| `.env.example` | Empty credential variable |
| `src/scrape_gateway/cli.py` | `PROVIDER_API_KEYS` entry for `sgw setup` |
| `tests/test_discovery.py` | `SHIPPED_PROVIDERS` and paid-provider classification |
| `tests/test_additional_api_providers.py` | Cost, auth, parameter, status, and failure tests |
| `README.md` | Provider count and capability row |
| `docs/providers.md` | Capability row plus exact wire/cost contract |
| `docs/configuration.md` | Credential variable |
| `docs/SKILL.md` | Provider table/count/country summary |
| `src/scrape_gateway/provider_contracts/v1/<name>.json` | Versioned provider wire contract |
| `tests/test_provider_contracts.py` | Schema and adapter/contract parity |

Only advertise capabilities that the adapter actually maps. The README matrix test
checks the declared capability set against the table.

## Adapter contract

- Set `name`, `cost_rank`, `capabilities`, and `required_configuration`.
- Return `provider_unavailable` without making a request when credentials are absent.
- Set `is_free = True` only for a genuinely free call. Otherwise implement
  `estimated_cost_units()` as a conservative upper bound for every supported shape.
- When the provider reports billed units, add an `AttemptLedgerEntry` with
  `cost_provenance="exact"`; fall back to the shape estimate only when the report is
  absent or invalid.
- Distinguish the provider API's HTTP status from the target page's status. Validate
  against the target status and content; retain the provider status in metadata.
- Map auth rejection to `provider_unavailable`, provider rate limiting to `http_429`,
  timeouts to `timeout`, target blocks to the target failure, and unexplained API
  failures to `provider_error`.
- Never forward the router-generated browser identity to a managed browser/proxy.
  Pass only `caller_headers(request.headers)` using the provider's documented scheme.
- Clamp provider parameters to documented ranges. Do not silently claim support for a
  wait, screenshot, Markdown, mobile, or header option that is discarded.
- Give materially different billed routes distinct names such as
  `<provider>:residential`.

Use the closest existing adapter as a structural reference, but verify every wire field
against the new provider's own documentation.

## Fast verification order

Run the small, diagnostic checks first:

```bash
uv run pytest -q tests/test_additional_api_providers.py tests/test_discovery.py
uv run ruff check src/scrape_gateway/providers/<name>.py tests/test_additional_api_providers.py tests/test_discovery.py
uv run ruff format --check src/scrape_gateway/providers/<name>.py tests/test_additional_api_providers.py tests/test_discovery.py
uv run sgw providers
```

Then run the one canonical non-paid gate from the repository root:

```bash
uv run pytest -q --ignore=tests/test_scraperapi_live.py --ignore=tests/test_scrapedrive_live.py
uv run ruff check .
uv run ruff format --check .
```

The full suite includes public-site extraction tests. In a network-restricted sandbox
it stalls at `tests/test_extract.py::TestExtractLive`; run the canonical command with
network access immediately instead of waiting or restarting it blindly. The two paid
live files must remain excluded.

Finally, force one cheapest-shape live request with the new provider and `--no-cache`.
Confirm the provider, route, target status, reported cost, validation result, and saved
HTML/Markdown paths. One successful HTTP request alone is not enough.

## Commit, release, and installed proof

1. Commit the working provider and docs as one feature checkpoint.
2. A new provider is a minor release: bump `pyproject.toml`, update the README badge and
   `CHANGELOG.md`, then commit `Release vX.Y.0`.
3. Tag, push `main`, push the tag, and create the GitHub release.
4. Preserve installed extensions when refreshing the CLI:

   ```bash
   uv tool install --reinstall \
     --with ./extensions/sg-playwright \
     --with ./extensions/sg-browserless .
   ```

5. Verify the installed package version, `sgw providers`, one live scrape through the
   installed binary, the remote `main` and tag SHA, and exact-SHA GitHub Actions success.

For a provider-only request, stop here. Do not imply that `sev`, a remote host, or a
deployment was updated unless each was changed and verified explicitly.
