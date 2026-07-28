from scrape_gateway.models import AttemptLedgerEntry, FailureReason, ScrapeResult


def test_attempt_ledger_entry_is_exported_from_public_package() -> None:
    from scrape_gateway import AttemptLedgerEntry as PublicAttemptLedgerEntry

    assert PublicAttemptLedgerEntry is AttemptLedgerEntry


def test_attempt_ledger_serializes_plain_values_and_computes_run_total() -> None:
    first = AttemptLedgerEntry(
        provider="scrapedrive",
        route="scrapedrive:standard",
        cost_units=1,
        cost_provenance="estimated",
        success=False,
        latency_ms=120,
        status_code=403,
        failure_reason=FailureReason.HTTP_403,
        block_type=None,
    )
    second = AttemptLedgerEntry(
        provider="scrapfly",
        route="scrapfly:asp",
        cost_units=19,
        cost_provenance="exact",
        success=True,
        latency_ms=240,
        status_code=200,
        failure_reason=None,
        block_type=None,
    )
    result = ScrapeResult(
        url="https://example.com",
        provider="scrapfly",
        success=True,
        cost_units=19,
        attempt_ledger=[first, second],
    )

    assert first.to_dict() == {
        "provider": "scrapedrive",
        "route": "scrapedrive:standard",
        "cost_units": 1,
        "cost_provenance": "estimated",
        "success": False,
        "latency_ms": 120,
        "status_code": 403,
        "failure_reason": "http_403",
        "block_type": None,
    }
    assert result.cost_units == 19
    assert result.run_cost_units == 20


def test_run_cost_falls_back_to_legacy_cost_units_when_ledger_is_empty() -> None:
    legacy_result = ScrapeResult(
        url="https://example.com",
        provider="third_party",
        success=True,
        cost_units=7,
    )
    cache_result = ScrapeResult(
        url="https://example.com",
        provider="cache",
        success=True,
    )

    assert legacy_result.run_cost_units == 7
    assert cache_result.run_cost_units == 0
