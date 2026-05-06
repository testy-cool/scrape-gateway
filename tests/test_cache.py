import json
import tempfile
import time

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


def test_saves_meta_json():
    with tempfile.TemporaryDirectory() as tmp:
        cache = ArtifactCache(root=tmp)
        result = ScrapeResult(
            url="https://example.com", provider="test", success=True, html="<html>x</html>"
        )
        cache.save(result)
        meta_path = cache.paths_for_url("https://example.com")["meta"]
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["provider"] == "test"
        assert "fetched_at" in meta


def test_ttl_expired():
    with tempfile.TemporaryDirectory() as tmp:
        cache = ArtifactCache(root=tmp, ttl_seconds=1)
        result = ScrapeResult(
            url="https://example.com", provider="test", success=True, html="<html>x</html>"
        )
        cache.save(result)
        # Backdate the meta to make it expired
        meta_path = cache.paths_for_url("https://example.com")["meta"]
        meta = json.loads(meta_path.read_text())
        meta["fetched_at"] = time.time() - 10
        meta_path.write_text(json.dumps(meta))
        assert cache.get_html("https://example.com") is None


def test_ttl_not_expired():
    with tempfile.TemporaryDirectory() as tmp:
        cache = ArtifactCache(root=tmp, ttl_seconds=3600)
        result = ScrapeResult(
            url="https://example.com", provider="test", success=True, html="<html>fresh</html>"
        )
        cache.save(result)
        assert cache.get_html("https://example.com") == "<html>fresh</html>"
