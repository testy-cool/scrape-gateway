import json
from pathlib import Path

import pytest

from scrape_gateway.validators import validate_content


_CONSENT_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "consent_validation_cases.json").read_text()
)


def _saved_fixture_html(case: dict[str, object]) -> str:
    """Restore captured length without checking whole third-party pages into git."""
    excerpt = str(case["excerpt"])
    content_chars = int(case["content_chars"])
    assert len(excerpt) <= content_chars
    return excerpt + "x" * (content_chars - len(excerpt))


def test_valid_page():
    html = "<html><body><h1>Product Page</h1><p>This is a real product with lots of content.</p></body></html>"
    result = validate_content(html)
    assert result.passed
    assert not result.checks_failed


def test_empty_content():
    result = validate_content("")
    assert not result.passed
    assert result.block_type == "empty_content"
    assert "min_text_chars" in result.checks_failed


def test_short_content():
    result = validate_content("<html>hi</html>")
    assert not result.passed
    assert result.block_type == "empty_content"


def test_cloudflare_block():
    html = (
        "<html><body>Checking your browser before accessing the site. Please wait...</body></html>"
        + "x" * 200
    )
    result = validate_content(html)
    assert not result.passed
    assert result.block_type == "cloudflare"
    assert "block_signatures" in result.checks_failed


def test_captcha_block():
    html = (
        "<html><body>Please solve the g-recaptcha challenge to continue browsing.</body></html>"
        + "x" * 200
    )
    result = validate_content(html)
    assert not result.passed
    assert result.block_type == "captcha"


def test_js_required():
    html = (
        "<html><body>You need to enable javascript to view this page content.</body></html>"
        + "x" * 200
    )
    result = validate_content(html)
    assert not result.passed
    assert result.block_type == "js_shell"


def test_create_account_marketing_copy_not_login_wall():
    html = (
        "<html><body><h1>Developer platform</h1>"
        "<p>Create an account to get started. You can set up an org for your team later.</p>"
        "<p>This is normal public homepage content with enough text to pass validation.</p>"
        "</body></html>"
    )
    result = validate_content(html)
    assert result.passed


@pytest.mark.parametrize(
    "case",
    [case for case in _CONSENT_CASES if case["classification"] == "full_content"],
    ids=lambda case: case["id"],
)
def test_cookie_discussion_on_full_page_is_not_a_consent_wall(case):
    html = _saved_fixture_html(case)

    assert len(html) == case["content_chars"]
    assert case["matched_pattern"] in html.lower()
    result = validate_content(html)

    assert result.passed
    assert result.block_type is None


@pytest.mark.parametrize(
    "case",
    [case for case in _CONSENT_CASES if case["classification"] == "consent_wall"],
    ids=lambda case: case["id"],
)
def test_short_consent_wall_fixture_is_still_rejected(case):
    html = _saved_fixture_html(case)

    assert len(html) == case["content_chars"]
    result = validate_content(html)

    assert not result.passed
    assert result.block_type == "consent_wall"
    assert result.matched_pattern == case["matched_pattern"]


def test_must_not_contain():
    html = "<html><body>" + "x" * 200 + "forbidden phrase here</body></html>"
    result = validate_content(html, must_not_contain=["forbidden phrase"])
    assert not result.passed
    assert "must_not_contain" in result.checks_failed


def test_must_contain_any_passes():
    html = "<html><body>" + "x" * 200 + "this has reviews in it</body></html>"
    result = validate_content(html, must_contain_any=["reviews", "pricing"])
    assert result.passed


def test_must_contain_any_fails():
    html = "<html><body>" + "x" * 200 + "nothing relevant here</body></html>"
    result = validate_content(html, must_contain_any=["reviews", "pricing"])
    assert not result.passed
    assert "must_contain_any" in result.checks_failed


def test_custom_min_chars():
    html = "short"
    result = validate_content(html, min_text_chars=3)
    assert result.passed


def test_none_input():
    result = validate_content(None)
    assert not result.passed
    assert result.block_type == "empty_content"
