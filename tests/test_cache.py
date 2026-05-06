import tempfile

from scrape_gateway.cache import ArtifactCache
from scrape_gateway.models import ScrapeResult


def test_save_and_get_html():
    with tempfile.TemporaryDirectory() as tmp:
        cache = ArtifactCache(root=tmp)
        result = ScrapeResult(
            url="https://example.com",
            provider="test",
            success=True,
            html="<html><body>hello</body></html>",
        )
        cache.save(result)
        assert cache.get_html("https://example.com") == "<html><body>hello</body></html>"


def test_cache_miss():
    with tempfile.TemporaryDirectory() as tmp:
        cache = ArtifactCache(root=tmp)
        assert cache.get_html("https://not-cached.com") is None


def test_saves_markdown():
    with tempfile.TemporaryDirectory() as tmp:
        cache = ArtifactCache(root=tmp)
        result = ScrapeResult(
            url="https://example.com",
            provider="test",
            success=True,
            html="<html><body><h1>Title</h1></body></html>",
        )
        cache.save(result)
        paths = cache.paths_for_url("https://example.com")
        assert paths["markdown"].exists()
        assert "Title" in paths["markdown"].read_text()
