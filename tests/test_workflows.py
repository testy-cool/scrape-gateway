import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_every_action_is_pinned_to_a_full_commit_sha() -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith("uses:"):
                continue
            reference = stripped.split("#", 1)[0].split("@", 1)[-1].strip()
            assert FULL_SHA.fullmatch(reference), f"{path.name}: unpinned action {stripped}"


def test_ci_runs_the_safe_gates_on_all_supported_pythons() -> None:
    workflow = _workflow("ci.yml")
    assert "permissions:\n  contents: read" in workflow
    assert 'python-version: ["3.11", "3.12", "3.13"]' in workflow
    assert "run: make check" in workflow
    assert "run: make package-check" in workflow
    assert "run: make image-check" in workflow
    assert "secrets." not in workflow


def test_ci_exposes_stable_required_check_names() -> None:
    workflow = _workflow("ci.yml")
    assert "name: quality (${{ matrix.python-version }})" in workflow
    assert "name: package" in workflow
    assert "name: container" in workflow
