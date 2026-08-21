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
directory owns its environment configuration and a `compose.production.yml` file with a service
named `sgw` that consumes `SGW_IMAGE`. Provider credentials stay in that directory's private
configuration; release automation never reads or copies them. The deployer maintains a dedicated
non-secret `.sgw-image.env` containing only the immutable `SGW_IMAGE` reference.

## Deploy and rollback contract

The deploy workflow records the currently configured immutable digest, writes the requested
released digest, pulls and starts that exact image, waits for health, and verifies both the running
digest and application version. If any step fails, it restores the recorded digest and verifies
health again.

Manual rollback accepts only a prior `sha256:<64 lowercase hex>` digest. It uses the same protected
environment and verification path. A rollback changes only the image digest; persisted cache,
telemetry, memory, and operator configuration remain in place.

Every production `sgw` container must define a health check. Verification requires healthy status,
the exact configured digest in the running container, that digest in the local image's repository
digests, and the release version label. A failed candidate automatically restores and re-verifies
the previously recorded digest.

After selecting a digest already published by the release workflow, an authorized operator may
start a manual rollback without exposing deployment details:

```bash
gh workflow run rollback.yml -f digest=sha256:<64-lowercase-hex>
```

Production approval, environment secrets, and environment reviewers are GitHub settings. Do not
invent reviewers or placeholder credentials in repository files. See [RELEASING.md](RELEASING.md)
for how immutable images are produced.
