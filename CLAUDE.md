# scrape-gateway agent notes

All durable repository policy is tool-neutral:

- [CONTRIBUTING.md](CONTRIBUTING.md) — setup, safe tests, commit discipline, and proof levels
- [RELEASING.md](RELEASING.md) — versioning, immutable publication, and release recovery
- [DEPLOYMENT.md](DEPLOYMENT.md) — production approvals, digest deployment, and rollback
- [Built-in provider workflow](docs/references/adding-built-in-provider.md)
- [ScrapingEvals passive-feed contract](docs/scrapingevals-feed.md)

Read those files before changing the repository. They are canonical when this agent-only note
drifts.

After a committed change to commands, flags, configuration, or provider development, sync the
changed tracked skill files from `docs/SKILL.md` and `docs/references/` to the installed
`~/.agents/skills/scrape-gateway/` copy. The tracked repository remains the source of truth.
