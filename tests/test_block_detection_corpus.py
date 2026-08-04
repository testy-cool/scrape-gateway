import json
from pathlib import Path

import pytest

from scrape_gateway.errors import classify_failure
from scrape_gateway.validators import validate_content

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "block_detection"
_CASES = json.loads((_FIXTURE_ROOT / "cases.json").read_text())


def _fixture_html(case: dict[str, object]) -> str:
    html = (_FIXTURE_ROOT / str(case["file"])).read_text()
    content_chars = int(case.get("content_chars", len(html)))
    assert len(html) <= content_chars
    return html + "x" * (content_chars - len(html))


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case["id"])
def test_block_detection_fixture_corpus(case):
    html = _fixture_html(case)

    validation = validate_content(html)

    assert validation.passed is case["validator_passed"]
    assert validation.block_type == case["block_type"]
    if validation.passed:
        assert classify_failure(200, html) is None
    else:
        assert classify_failure(200, html) is not None


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case["id"])
def test_classifier_never_rejects_a_fixture_that_the_validator_passes(case):
    html = _fixture_html(case)

    if validate_content(html).passed:
        assert classify_failure(200, html) is None
