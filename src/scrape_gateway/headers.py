from __future__ import annotations

from collections.abc import Mapping


_BROWSER_MANAGED_HEADERS = {
    "accept",
    "priority",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-site",
    "sec-fetch-user",
    "upgrade-insecure-requests",
    "user-agent",
}


def browser_context_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return only headers that are safe to reuse for browser subrequests.

    Browser contexts apply extra headers to every request, not only the top-level
    navigation. Chromium and Firefox must derive fetch metadata, Accept, priority,
    and user-agent headers themselves for each resource and browser fingerprint.
    """
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in _BROWSER_MANAGED_HEADERS
    }
