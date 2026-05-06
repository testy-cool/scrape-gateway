import tempfile
from pathlib import Path

from scrape_gateway.memory import DomainMemory


def test_remember_and_recall():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        mem.remember_success("https://www.example.com/page", "scrapingbee", "us", True, False)
        assert mem.preferred_provider("https://www.example.com/other") == "scrapingbee"


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
        row = mem.conn.execute(
            "select success_count from domain_routes where domain = ?", ("example.com",)
        ).fetchone()
        assert row[0] == 2
