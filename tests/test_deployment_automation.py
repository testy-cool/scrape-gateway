import importlib.util
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
IMAGE = "ghcr.io/testy-cool/scrape-gateway"
OLD_DIGEST = "sha256:" + "a" * 64
NEW_DIGEST = "sha256:" + "b" * 64
BAD_DIGEST = "sha256:" + "d" * 64


def _digest_module():
    path = ROOT / "scripts" / "validate_image_digest.py"
    spec = importlib.util.spec_from_file_location("validate_image_digest", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "digest",
    ["latest", "sha256:abc", "SHA256:" + "a" * 64, "sha256:" + "g" * 64],
)
def test_mutable_or_malformed_rollback_references_are_rejected(digest: str) -> None:
    with pytest.raises(ValueError, match="digest must match"):
        _digest_module().image_reference(digest)


def test_digest_resolves_to_the_canonical_immutable_image() -> None:
    assert _digest_module().image_reference(OLD_DIGEST) == f"{IMAGE}@{OLD_DIGEST}"


def _fake_docker(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import sys
from pathlib import Path

args = sys.argv[1:]
reference = Path('.sgw-image.env').read_text().strip().split('=', 1)[1]
digest = reference.split('@', 1)[1]
if args[0] == 'compose':
    if 'ps' in args:
        print('fake-container')
elif args[0] == 'inspect':
    fmt = args[args.index('--format') + 1]
    if 'Health.Status' in fmt:
        print('unhealthy' if digest.endswith('d' * 64) else 'healthy')
    elif 'Config.Image' in fmt:
        print(reference)
    elif '.Image' in fmt:
        print('sha256:' + 'f' * 64)
elif args[:2] == ['image', 'inspect']:
    fmt = args[args.index('--format') + 1]
    if 'RepoDigests' in fmt:
        print(reference)
    elif 'image.version' in fmt:
        print('0.30.0')
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _run_deploy(tmp_path: Path, digest: str) -> subprocess.CompletedProcess[str]:
    deploy_dir = tmp_path / "deployment"
    deploy_dir.mkdir()
    (deploy_dir / "compose.production.yml").write_text("services: {}\n", encoding="utf-8")
    (deploy_dir / ".sgw-image.env").write_text(
        f"SGW_IMAGE={IMAGE}@{OLD_DIGEST}\n", encoding="utf-8"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_docker(bin_dir / "docker")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SGW_HEALTH_ATTEMPTS": "1",
        "SGW_HEALTH_INTERVAL": "0",
    }
    return subprocess.run(
        [
            "sh",
            "scripts/deploy_image.sh",
            str(deploy_dir),
            IMAGE,
            digest,
            "0.30.0",
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_healthy_candidate_becomes_the_recorded_digest(tmp_path: Path) -> None:
    result = _run_deploy(tmp_path, NEW_DIGEST)
    assert result.returncode == 0, result.stderr
    assert "deployment=healthy" in result.stdout
    image_env = tmp_path / "deployment" / ".sgw-image.env"
    assert image_env.read_text(encoding="utf-8") == f"SGW_IMAGE={IMAGE}@{NEW_DIGEST}\n"


def test_unhealthy_candidate_automatically_restores_old_digest(tmp_path: Path) -> None:
    result = _run_deploy(tmp_path, BAD_DIGEST)
    assert result.returncode == 1
    assert f"rollback=restored digest={OLD_DIGEST}" in result.stdout
    image_env = tmp_path / "deployment" / ".sgw-image.env"
    assert image_env.read_text(encoding="utf-8") == f"SGW_IMAGE={IMAGE}@{OLD_DIGEST}\n"


def test_workflows_gate_deploy_and_rollback_on_production_environment() -> None:
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    rollback = (ROOT / ".github" / "workflows" / "rollback.yml").read_text(encoding="utf-8")
    assert "name: deploy production" in release
    assert "needs: [validate, image, publish]" in release
    assert "environment: production" in release
    assert "environment: production" in rollback
    assert "workflow_dispatch:" in rollback
    assert "scripts/validate_image_digest.py" in rollback

    secret_names = set(re.findall(r"secrets\.(PRODUCTION_[A-Z_]+)", release + rollback))
    assert secret_names == {
        "PRODUCTION_SSH_HOST",
        "PRODUCTION_SSH_PORT",
        "PRODUCTION_SSH_USER",
        "PRODUCTION_SSH_KEY",
        "PRODUCTION_DEPLOY_DIR",
    }
