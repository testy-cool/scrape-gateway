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
- Lint gates are `ruff check .` and `ruff format --check .`. The dev dependency is pinned
  below 0.16 because ruff 0.16 expanded its default rule set; see the pin in `pyproject.toml`.

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

## Config Files (not committed)

- `.env` — API keys (gitignored)
- `scrape-gateway.yml` — local provider enable/disable (not tracked, varies per machine)
