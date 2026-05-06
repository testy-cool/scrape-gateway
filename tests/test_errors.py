from scrape_gateway.errors import classify_failure
from scrape_gateway.models import FailureReason


def test_403():
    assert classify_failure(403) == FailureReason.HTTP_403


def test_429():
    assert classify_failure(429) == FailureReason.HTTP_429


def test_5xx():
    assert classify_failure(500) == FailureReason.HTTP_5XX
    assert classify_failure(503) == FailureReason.HTTP_5XX


def test_empty_content():
    assert classify_failure(200, "") == FailureReason.EMPTY_CONTENT
    assert classify_failure(200, "   ") == FailureReason.EMPTY_CONTENT


def test_captcha():
    assert (
        classify_failure(200, "please solve the captcha to continue" + "x" * 100)
        == FailureReason.CAPTCHA
    )


def test_cloudflare():
    body = "Checking your browser before accessing the site" + "x" * 100
    assert classify_failure(200, body) == FailureReason.CLOUDFLARE


def test_js_required():
    body = "You need to enable javascript to view this page" + "x" * 100
    assert classify_failure(200, body) == FailureReason.JS_REQUIRED


def test_clean_page():
    body = "<html><body>Hello world, this is real content with enough text to pass the minimum threshold check.</body></html>"
    assert classify_failure(200, body) is None
