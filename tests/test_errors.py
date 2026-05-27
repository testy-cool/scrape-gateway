from scrape_gateway.errors import classify_exception, classify_failure
from scrape_gateway.models import FailureReason


def test_403():
    assert classify_failure(403) == FailureReason.HTTP_403


def test_429():
    assert classify_failure(429) == FailureReason.HTTP_429


def test_5xx():
    assert classify_failure(500) == FailureReason.HTTP_5XX
    assert classify_failure(503) == FailureReason.HTTP_5XX


def test_407_proxy_error():
    assert classify_failure(407) == FailureReason.PROXY_ERROR


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


def test_login_required_short_page():
    body = "Please sign in with your password to continue" + "x" * 200
    assert classify_failure(200, body) == FailureReason.LOGIN_REQUIRED


def test_login_form_in_nav_not_flagged():
    """phpBB forums and similar sites have login forms in the nav but content is public."""
    body = '<nav>sign in password</nav>' + '<div class="content">' + 'x' * 10000 + '</div>'
    assert classify_failure(200, body) is None


def test_clean_page():
    body = "<html><body>Hello world, this is real content with enough text to pass the minimum threshold check.</body></html>"
    assert classify_failure(200, body) is None


def test_proxy_exception():
    assert classify_exception(Exception("407 Proxy Authentication Required")) == FailureReason.PROXY_ERROR
    assert classify_exception(Exception("ProxyAuthRequired")) == FailureReason.PROXY_ERROR
