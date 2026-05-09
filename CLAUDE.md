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
- `uv run pytest tests/ -v --ignore=tests/test_scraperapi_live.py --ignore=tests/test_scrapedrive_live.py` — unit tests (must pass)
- Live tests need API keys in `.env` and hit real services — run manually when touching providers

## Project Layout

- `src/scrape_gateway/` — package source
- `src/scrape_gateway/providers/` — all provider implementations (discovered via entry points)
- `src/scrape_gateway/discovery.py` — provider discovery (entry points + local extensions)
- `tests/` — pytest suite (136+ unit tests, live tests per paid provider)
- `docs/SKILL.md` — Claude Code skill (keep in sync with `~/.claude/skills/scrape-gateway/SKILL.md`)
- `examples/` — extension example
- `registry.yml` — curated extension registry

## Config Files (not committed)

- `.env` — API keys (gitignored)
- `scrape-gateway.yml` — local provider enable/disable (not tracked, varies per machine)
