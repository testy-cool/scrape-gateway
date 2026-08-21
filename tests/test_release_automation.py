import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_current_version_passes_release_identity_guard() -> None:
    guard = _load_script("release_guard.py")
    version = guard.project_version()
    assert guard.validate_release(f"v{version}", _head(), "HEAD") == {
        "tag": f"v{version}",
        "version": version,
        "revision": _head(),
    }


@pytest.mark.parametrize("tag", ["0.30.0", "v0.30", "v01.2.3", "v1.2.3-rc1", "release"])
def test_malformed_release_tags_are_rejected(tag: str) -> None:
    guard = _load_script("release_guard.py")
    with pytest.raises(ValueError, match="tag must match"):
        guard.validate_release(tag, _head())


def test_tag_version_mismatch_is_rejected_before_publication() -> None:
    guard = _load_script("release_guard.py")
    with pytest.raises(ValueError, match="does not match project version"):
        guard.validate_release("v999.0.0", _head())


def test_required_checks_accept_only_the_complete_green_set() -> None:
    checks = _load_script("verify_required_checks.py")
    payload = {
        "check_runs": [
            {"name": name, "status": "completed", "conclusion": "success"}
            for name in checks.REQUIRED_CHECKS
        ]
    }
    assert checks.evaluate(payload) == {name: "success" for name in checks.REQUIRED_CHECKS}
    payload["check_runs"].pop()
    with pytest.raises(ValueError, match="required checks are not green"):
        checks.evaluate(payload)


def test_release_workflow_has_a_non_publishing_dispatch_path() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "dry_run:" in workflow
    assert "if: github.event_name == 'push'" in workflow
    assert "push: ${{ github.event_name == 'push' }}" in workflow
    assert workflow.index("scripts/release_guard.py") < workflow.index("docker/build-push-action@")


def test_release_workflow_publishes_only_github_and_ghcr_assets() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'tags: ["v*.*.*"]' in workflow
    assert "packages: write" in workflow
    assert "contents: write" in workflow
    assert "environment: release" in workflow
    assert "gh release create" in workflow
    assert "SHA256SUMS" in workflow
    assert "@${{ steps.digest.outputs.value }}" not in workflow
    assert "pypi" not in workflow.lower()


def test_release_provenance_contains_hashes_without_artifact_bodies(tmp_path: Path) -> None:
    artifact = tmp_path / "example.whl"
    artifact.write_bytes(b"package")
    output = tmp_path / "provenance.json"
    result = subprocess.run(
        [
            "python",
            "scripts/write_release_provenance.py",
            "--dist",
            str(tmp_path),
            "--tag",
            "v0.30.0",
            "--revision",
            "a" * 40,
            "--repository",
            "testy-cool/scrape-gateway",
            "--workflow-run",
            "https://github.com/testy-cool/scrape-gateway/actions/runs/1",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["artifacts"] == [
        {
            "name": "example.whl",
            "bytes": 7,
            "sha256": "bc4a71180870f7945155fbb02f4b0a2e3faa2a62d6d31b7039013055ed19869a",
        }
    ]
