# Releasing scrape-gateway

This file is the canonical release policy. Releases are GitHub Releases plus GHCR images; this
repository does **not** publish to PyPI.

## Version policy

The version in `pyproject.toml`, the `vX.Y.Z` tag, the README version badge, and the newest
`CHANGELOG.md` heading must agree.

- breaking CLI, Python, configuration, or provider-interface change: major;
- new provider, command, flag, or configuration behavior: minor;
- compatible fix or correction: patch;
- prose-only typo or internal refactor with no behavior change: no release required.

Never move, recreate, overwrite, amend, or force-push an existing release tag. Correct a bad
release by reverting or fixing it in a new version.

## Release preconditions

1. Start from a clean `main` at the exact remote revision.
2. Run the full non-live checks, package checks, and container checks described in
   [CONTRIBUTING.md](CONTRIBUTING.md).
3. Update `pyproject.toml`, the README badge, and `CHANGELOG.md` in a `Release vX.Y.Z` commit.
4. Confirm the release commit is on protected `main` and its required checks are green.
5. Create and push only the matching annotated `vX.Y.Z` tag.

The tag workflow must reject a malformed tag, a tag/version mismatch, a commit outside `main`,
or a commit without successful required checks before it publishes anything. It builds the wheel
and source distribution once, installs those exact files in clean environments, emits
`SHA256SUMS` and provenance, creates the GitHub Release, and publishes the corresponding GHCR
image by immutable digest.

Before creating a real tag, exercise the same validation, artifact installation, and image build
without publication from GitHub Actions:

```bash
gh workflow run release.yml --ref main -f dry_run=true -f tag=vX.Y.Z
```

The supplied tag must match the current `pyproject.toml` version. Dispatch runs never write a
GitHub Release or GHCR tag; only a newly pushed matching tag can enter the publish job.

## Verification and recovery

After publication, verify the GitHub Release assets and checksums, package-install smoke, GHCR
version and full-commit tags, immutable digest, and image labels. Installation and deployment are
separate follow-up operations; publishing a release does not prove either happened.

If publication is incomplete, leave the existing tag immutable, document the failure, and issue a
new patch release. If a deployment fails, use the deployment rollback workflow with the previously
recorded digest; do not rewrite the release.
