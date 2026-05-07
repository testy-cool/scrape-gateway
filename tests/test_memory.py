import tempfile
from pathlib import Path

from scrape_gateway.memory import DomainMemory


def test_remember_and_recall():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        mem.remember_success("https://www.example.com/page", "scrapingbee", "us", True, False)
        assert mem.preferred_provider("https://www.example.com/other") == ("scrapingbee", None)


def test_no_memory():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        assert mem.preferred_provider("https://unknown.com") is None


def test_domain_extraction():
    assert DomainMemory.domain_for_url("https://www.example.com/path") == "example.com"
    assert DomainMemory.domain_for_url("https://sub.example.com") == "sub.example.com"


def test_success_count_increments():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        mem.remember_success("https://example.com/a", "raw_http", None, False, False)
        mem.remember_success("https://example.com/b", "raw_http", None, False, False)
        stats = mem.provider_stats("https://example.com")
        assert len(stats) == 1
        assert stats[0]["provider"] == "raw_http"
        assert stats[0]["success_count"] == 2


def test_remember_failure():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        mem.remember_failure("https://example.com/a", "raw_http")
        mem.remember_failure("https://example.com/b", "raw_http")
        stats = mem.provider_stats("https://example.com")
        assert stats[0]["failure_count"] == 2
        assert stats[0]["success_count"] == 0


def test_remember_block():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        mem.remember_failure("https://example.com/a", "raw_http", block_type="cloudflare")
        stats = mem.provider_stats("https://example.com")
        assert stats[0]["block_count"] == 1
        assert stats[0]["last_block_type"] == "cloudflare"


def test_should_skip_after_repeated_failures():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        for _ in range(5):
            mem.remember_failure("https://hard.com/page", "raw_http")
        assert mem.should_skip_provider("https://hard.com/other", "raw_http") is True


def test_should_not_skip_with_no_history():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        assert mem.should_skip_provider("https://new.com", "raw_http") is False


def test_should_not_skip_with_good_success_rate():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        for _ in range(10):
            mem.remember_success("https://mixed.com/a", "raw_http", None, False, False)
        mem.remember_failure("https://mixed.com/b", "raw_http")
        assert mem.should_skip_provider("https://mixed.com/x", "raw_http") is False


def test_prefers_provider_with_better_record():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        mem.remember_success("https://example.com/a", "raw_http", None, False, False)
        for _ in range(5):
            mem.remember_success("https://example.com/b", "scrapedrive", "us", False, False)
        assert mem.preferred_provider("https://example.com") == ("scrapedrive", None)


def test_blocks_penalized_harder():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        mem.remember_success("https://example.com/a", "raw_http", None, False, False)
        mem.remember_failure("https://example.com/b", "raw_http", block_type="cloudflare")
        mem.remember_success("https://example.com/c", "scrapedrive", "us", False, False)
        # raw_http: 1 success - (0 failures + 1 block * 3) = -2
        # scrapedrive: 1 success - 0 = 1
        assert mem.preferred_provider("https://example.com") == ("scrapedrive", None)


def test_preferred_provider_returns_tier():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        mem.remember_success(
            "https://example.com/a", "scrapedrive", "us", False, True, tier="scrapedrive:advanced"
        )
        result = mem.preferred_provider("https://example.com")
        assert result == ("scrapedrive", "scrapedrive:advanced")


def test_preferred_provider_returns_none_tuple_when_no_tier():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        mem.remember_success("https://example.com/a", "raw_http", None, False, False)
        result = mem.preferred_provider("https://example.com")
        assert result == ("raw_http", None)


def test_preferred_provider_returns_none_when_no_history():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        result = mem.preferred_provider("https://unknown.com")
        assert result is None


def test_stores_tier_info():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        mem.remember_success(
            "https://example.com/a", "scrapedrive", "us", False, True, tier="scrapedrive:advanced"
        )
        stats = mem.provider_stats("https://example.com")
        assert stats[0]["last_success_tier"] == "scrapedrive:advanced"
        assert stats[0]["last_success_country"] == "us"
