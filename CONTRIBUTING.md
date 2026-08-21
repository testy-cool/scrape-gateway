# Contributing to scrape-gateway

This is the canonical contribution guide for humans and coding agents. Start here from a
fresh clone; no machine-local instructions or installed skills are required.

## Public-repository boundary

Everything committed here is public. Never commit credentials, private service hostnames,
server addresses, SSH aliases, deployment directories, service identifiers, container names,
or reverse-proxy topology. Keep credentials in ignored `.env` files and describe deployments
only through generic environment-variable interfaces.

## Development setup

```bash
git clone https://github.com/testy-cool/scrape-gateway.git
cd scrape-gateway
uv sync --all-extras
```

Work from the repository root. Before editing, inspect `git status` and preserve unrelated
changes. Source code lives under `src/scrape_gateway/`; tests include both root `tests/` and
extension suites under `extensions/**`.

## Safe verification

The ordinary gate is offline and must not use provider credentials or make provider calls:

```bash
make check
```

Tests marked `live` access external services. Run them only when the task explicitly authorizes
network/provider activity. Never infer that credentials in `.env` grant permission to spend
credits.

Package and container verification are separate because they may download build dependencies or
base images, but they never call scraping providers:

```bash
make package-check
make image-check
```

The only repository target that runs external scraping tests is guarded explicitly:

```bash
ALLOW_LIVE=1 make live-check
```

For a built-in provider, follow the complete surface map in
[`docs/references/adding-built-in-provider.md`](docs/references/adding-built-in-provider.md).
Provider wire facts belong in the versioned machine-readable contract, not only in prose.

## Commits and reviews

- Split commits by effect and stage only owned files.
- Use imperative, behavior-focused subjects.
- Run the relevant focused checks and the full safe gate before committing.
- Normal work may land directly on `main`; risky or long-running work should use a pull request.
- Do not amend or retag published work, and never force-push `main`.

CI is expected to pass for Python 3.11, 3.12, and 3.13. Repository administrators should require
all documented CI checks on `main`, require the branch to be current before merge, block force
pushes and deletion, and require review for workflow-file changes.

## Source, package, install, and live proof

Keep these claims separate:

1. **Source proof**: focused tests and the complete safe gate pass in the checkout.
2. **Package proof**: the built wheel and source distribution install into a clean environment.
3. **Installed proof**: the installed `sgw` reports the expected version and provider inventory.
4. **Live proof**: an explicitly authorized request exercises the installed artifact or deployed
   image and records its revision. A local unit test is not live proof.

Installing with `uv tool` is separate from the repository environment. Reinstalling the main
package can remove optional extensions, so inventory the current install and reinstall required
extensions deliberately before claiming parity.

See [RELEASING.md](RELEASING.md) for versioned publication and
[DEPLOYMENT.md](DEPLOYMENT.md) for production boundaries and rollback.
