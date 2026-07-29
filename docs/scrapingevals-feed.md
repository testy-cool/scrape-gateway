# SGW to ScrapingEvals passive evidence

## Outcome

Normal Scrape Gateway traffic already produces useful evidence. Provider calls are
appended to `.scrape-gateway/memory.sqlite`, while richer run and evaluator evidence is
written under `.scrape-gateway/runs/`. The `sgw scrapingevals` command turns that local
state into a versioned, privacy-safe staging feed:

```text
ordinary SGW scrape
  -> append-only provider-attempt ledger + run telemetry
  -> sgw scrapingevals
  -> private ScrapingEvals inbox
  -> schema validation and idempotent import
  -> human review/promotion
  -> static field-observations page
```

This is intentionally not a direct publish command. Opportunistic traffic can reveal
useful routing, cost, block, latency, artifact, and evaluator evidence, but it is not a
controlled benchmark. Every exported run is therefore labeled
`operational_observation`, `review_required`, and `comparable: false`.

## What goes where

| Owner | Path or artifact | Purpose |
| --- | --- | --- |
| Scrape Gateway | `.scrape-gateway/memory.sqlite` | Canonical append-only provider attempts and the stable source instance ID |
| Scrape Gateway | `.scrape-gateway/runs/<run-id>/` | Private reports, final HTML/Markdown, screenshots, and evaluator evidence |
| Transfer contract | `scrapingevals.sgw-observations/v1` JSON | Redacted run/attempt metadata, stable event IDs, cursor, counts, and artifact hashes |
| ScrapingEvals | `runner/data/sgw-inbox/v1/` | Immutable imported batches; private staging, never consumed directly by the site |
| ScrapingEvals | `runner/data/sgw-observations/v1/` | Validated and deduplicated observation records |
| ScrapingEvals | reviewed publication file | Explicitly promoted observations consumed by the static `/observations` page |
| ScrapingEvals | existing `runner/data/review-runs/` and catalog | Controlled corpus runs only; passive SGW traffic does not enter this contract |

The active ScrapingEvals site is a static export. Reviving the abandoned D1/Worker
implementation is not required for this pipeline.

## Exporting a feed

Create the first reviewed backfill:

```bash
mkdir -p /path/to/scrapingevals/runner/data/sgw-inbox/v1
sgw scrapingevals \
  --out /path/to/scrapingevals/runner/data/sgw-inbox/v1/backfill.json \
  --days 0
```

By default, URL paths are removed. When every target in the selected batch has been
reviewed as public and non-sensitive, paths can be retained:

```bash
sgw scrapingevals \
  --out /path/to/scrapingevals/runner/data/sgw-inbox/v1/reviewed.json \
  --days 30 \
  --include-url-paths
```

Incremental delivery uses the receiver's last acknowledged ledger row:

```bash
sgw scrapingevals \
  --out /path/to/scrapingevals/runner/data/sgw-inbox/v1/next.json \
  --days 0 \
  --after-ledger-id 1200 \
  --limit 1000
```

The receiver acknowledges `cursor.through_ledger_id` only after a batch validates and
is stored. If `cursor.has_more` is true, request another batch from that row. Replaying
the same batch is safe: each attempt has a stable
`<source.instance_id>:<ledger_id>` event ID.

## Feed contract

The top-level fields are:

- `schema`: `scrapingevals.sgw-observations/v1`
- `generated_at`: UTC snapshot time
- `source`: producer name, installed SGW version, and stable database instance ID
- `selection`: lookback and whether reviewed URL paths were included
- `privacy`: explicit transformations applied by the exporter
- `cursor`: requested lower bound, delivered high-watermark, and whether more rows exist
- `summary`: run, attempt, success, exclusion, and cost totals
- `runs`: grouped request/final/evaluator/artifact evidence
- `attempts`: one event per provider or provider-tier attempt

Attempt events preserve:

- ledger/run/attempt identity;
- country, JavaScript, premium, mobile, and screenshot request profile;
- provider and exact route/tier;
- success, HTTP status, normalized failure reason, block type, and latency;
- cost units plus exact-or-estimated provenance.

Run records add:

- start/finish/elapsed time when telemetry exists;
- final provider, route, validation state, and content-size signals;
- categorical evaluator status, calibration, verdict, checks, page type, root cause,
  recommendation, and modalities;
- artifact kind, byte size, and SHA-256 without artifact bodies or local paths.

## Privacy boundary

The default exporter:

- excludes local, private-IP, single-label, `.internal`, `.local`, `.test`,
  `.example`, `.invalid`, `.onion`, and similar special-use targets;
- removes URL credentials, query strings, and fragments;
- removes URL paths unless `--include-url-paths` is explicit;
- omits request headers, request metadata, response metadata, content, evaluator prose,
  generation IDs, and local artifact paths;
- hashes saved artifacts without copying their bodies.

ScrapingEvals still treats the result as private input. A public hostname or path can
carry customer or task context, and categorical model output can still be wrong.
Promotion therefore requires a human to confirm target safety, evidence meaning, and
the wording of any public claim.

## Publication rules

Passive observations can support statements such as:

- a route escalated through specific paid tiers before succeeding;
- a failed attempt consumed a recorded amount;
- the same request profile succeeded with a cheaper provider later;
- deterministic validation and an advisory evaluator agreed or disagreed.

They cannot support provider rankings or generalized success rates unless providers
ran the same declared corpus, geography, request profile, retry policy, time window,
and budget. Comparable work remains in ScrapingEvals' corpus runner and should link
back to the source feed events when SGW supplied the transport.

## Roadmap to a useful publication

1. **Producer contract** — ship `sgw scrapingevals`, its v1 schema, stable source/event
   identity, high-watermark cursor, privacy defaults, tests, and a real-ledger canary.
2. **Recoverable site source** — checkpoint the live Zenbook ScrapingEvals tree and
   push it to a private remote before adding another data path.
3. **Receiver** — add a dry-run-first ScrapingEvals importer that validates schema,
   rejects cursor/source conflicts, stores immutable inbox batches, and deduplicates
   events.
4. **First visible slice** — review the existing SGW observations, publish a small
   `/observations` page explaining the G2 tier escalation and the limits of the sample,
   and verify the production URL. Do not build a leaderboard.
5. **Passive delivery** — run export/import on a timer, keeping acknowledgement state
   in ScrapingEvals. Automation may stage data but may not promote it publicly.
6. **Comparable evidence** — run SGW against the canonical versioned corpus with fixed
   profiles and budgets, then promote those results into the existing controlled review
   contract.

## Known v1 limits

- Cache hits have telemetry but no provider-attempt ledger row, so they are not events in
  the v1 feed.
- Artifact bodies stay on the producer and need a separate reviewed copy step if a
  public observation should expose HTML, Markdown, or a screenshot.
- The feed carries evaluator categories, not evaluator prose or page excerpts.
- The source instance ID identifies one SQLite database. Copying that database preserves
  identity; starting a new database intentionally creates a new source.
