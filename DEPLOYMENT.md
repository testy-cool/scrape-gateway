# Deploying scrape-gateway

Docker Compose in the repository root is the local-development path: it builds the current
checkout, reads an ignored `.env`, exposes port 8100, and persists application data in the
`sgw-data` volume.

Production must deploy a released immutable image reference:

```text
SGW_IMAGE=ghcr.io/testy-cool/scrape-gateway@sha256:<digest>
```

Never deploy production from a mutable tag, a dirty checkout, or an unverified local build.

## Production interface

Production automation uses a protected GitHub `production` Environment and only these generic
repository/environment secret names:

- `PRODUCTION_SSH_HOST`
- `PRODUCTION_SSH_PORT`
- `PRODUCTION_SSH_USER`
- `PRODUCTION_SSH_KEY`
- `PRODUCTION_DEPLOY_DIR`

The repository must not contain their values or reveal the target's topology. The deployment
directory owns its environment configuration and a production Compose file that consumes
`SGW_IMAGE`; release automation never copies provider credentials.

## Deploy and rollback contract

The deploy workflow records the currently configured immutable digest, writes the requested
released digest, pulls and starts that exact image, waits for health, and verifies both the running
digest and application version. If any step fails, it restores the recorded digest and verifies
health again.

Manual rollback accepts only a prior `sha256:<64 lowercase hex>` digest. It uses the same protected
environment and verification path. A rollback changes only the image digest; persisted cache,
telemetry, memory, and operator configuration remain in place.

Production approval, environment secrets, and environment reviewers are GitHub settings. Do not
invent reviewers or placeholder credentials in repository files. See [RELEASING.md](RELEASING.md)
for how immutable images are produced.
