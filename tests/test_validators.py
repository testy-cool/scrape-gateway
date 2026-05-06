from scrape_gateway.validators import validate_content


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
