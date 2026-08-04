# scrape-gateway

## Release Process

**Every commit to main that changes user-facing behavior gets a release.** Don't ask — just do it.

After pushing commits that add features, fix bugs, or change the CLI/API surface:

1. Bump version in `pyproject.toml` (semver: breaking=major, feature=minor, fix=patch)
2. Add entry to `CHANGELOG.md` under a new `## [x.y.z] - YYYY-MM-DD` heading
3. Commit: `Release vX.Y.Z`
4. Tag and push: `git tag vX.Y.Z && git push && git push --tags`
5. Create GitHub release: `gh release create vX.Y.Z --title "vX.Y.Z" --notes-from-tag`

What counts as a release:
- New provider, command, or flag → minor bump
- Bug fix, test addition, doc update that fixes wrong info → patch bump
- Breaking change to CLI args, Python API, or provider interface → major bump

What does NOT need a release:
- README typos, internal refactors with no behavior change, test-only changes with no fixes

## Testing

Run before every commit:
- `uv run pytest -q --ignore=tests/test_scraperapi_live.py --ignore=tests/test_scrapedrive_live.py` — must pass

  Run it from the repo root with no path argument, exactly as CI does. Restricting it to
  `tests/` silently skips the extension test suites under `extensions/**`, which CI does
  collect — a green local run then turns red on the release commit.
- Never run bare `pytest`. Both `--ignore` flags are what keep the paid live-provider
  tests out; without them a routine test run spends real money.
- Live tests need API keys in `.env` and hit real services — run manually when touching providers
- Lint gates are `ruff check .` and `ruff format --check .`. The dev dependency now
  requires `ruff>=0.16`; the old `<0.16` ceiling was lifted on 2026-08-04.
- `uv sync` alone does **not** install ruff — it lives in the `dev` extra. Use
  `uv sync --all-extras`, or `uv pip install -e '.[dev]'` the way CI does.
- Two rules are switched off on purpose, both documented in `pyproject.toml`: `TRY004`,
  because validation failures raise `ValueError` deliberately and `web.py` converts those
  into JSON errors, and `B008` in `cli.py`, because `typer.Option(...)` in a default is
  the Typer idiom. Individual deliberate exceptions carry a `# noqa` with a reason
  instead of switching a rule off globally.

## Project Layout

- `src/scrape_gateway/` — package source
- `src/scrape_gateway/providers/` — all provider implementations (discovered via entry points)
- `src/scrape_gateway/discovery.py` — provider discovery (entry points + local extensions)
- `tests/` — pytest suite (136+ unit tests, live tests per paid provider)
- `docs/SKILL.md` — the agent skill. Copy it to `~/.agents/skills/scrape-gateway/SKILL.md`
  after any change that alters commands, flags, or config: Claude, Codex, and pi all
  symlink that one file, so a stale copy misinforms every agent at once. It drifted four
  releases behind before anyone noticed.
- `examples/` — extension example
- `registry.yml` — curated extension registry

## This Repo Is Public

`github.com/testy-cool/scrape-gateway` is a public repository. Everything committed
here is world-readable the moment it lands, including in docs and shell scripts.

Never commit deployment-specific detail:

- Live hostnames for our own services (`*.voidxd.cloud` and similar)
- Coolify application or service UUIDs, container names, network aliases
- SSH host aliases (`coolify-gen2`), server IPs, on-disk server paths
- Reverse-proxy config that maps our actual topology

Write self-hosting docs generically: `SGW_MCP_URL`, `SGW_MCP_TOKEN`, and the shape
of the deployment. Scripts must not default to one of our hosts; require the env
var instead. Operator runbooks belong in the private
`~/Work/claude-skills/project-index/references/` notes, not in `docs/`.

This is written down because `docs/mcp-coolify-ops.md` published the live MCP
hostname, the Coolify app UUID, the host alias, the Langfuse subdomain, and the
Caddy layout for two months before anyone noticed. No credential ever leaked and
every endpoint held at 401, but a force-push does not undo publication: GitHub
still serves orphaned commits by SHA after a history rewrite, so only a Support
request clears them. Keeping it out in the first place is the only cheap fix.

## Config Files (not committed)

- `.env` — API keys (gitignored)
- `scrape-gateway.yml` — local provider enable/disable (not tracked, varies per machine)
