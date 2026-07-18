from scrape_gateway.headers import browser_context_headers


def test_browser_context_headers_leave_browser_managed_headers_to_the_engine() -> None:
    source = {
        "Referer": "https://www.google.com/",
        "User-Agent": "synthetic desktop user agent",
        "Accept": "text/html,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Priority": "u=0, i",
        "Cache-Control": "max-age=0",
        "X-Test": "preserved",
    }

    filtered = browser_context_headers(source)

    assert filtered == {
        "Referer": "https://www.google.com/",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "max-age=0",
        "X-Test": "preserved",
    }
    assert source["Sec-Fetch-Dest"] == "document"
