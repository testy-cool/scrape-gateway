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


def test_get_result_restores_saved_markdown_and_screenshot():
    with tempfile.TemporaryDirectory() as tmp:
        cache = ArtifactCache(root=tmp)
        cache.save(
            ScrapeResult(
                url="https://example.com",
                provider="browserless",
                success=True,
                html="<html><body><h1>Current page</h1></body></html>",
                markdown="# Current page",
                screenshot=b"current-screenshot",
                route="browserless:content+screenshot",
            )
        )

        result = cache.get_result("https://example.com", require_screenshot=True)

        assert result is not None
        assert result.provider == "cache"
        assert result.route == "cache"
        assert result.markdown == "# Current page"
        assert result.screenshot == b"current-screenshot"
        assert result.metadata["cache_source_provider"] == "browserless"
        assert result.metadata["cache_source_route"] == "browserless:content+screenshot"


def test_get_result_rejects_html_only_entry_when_screenshot_is_required():
    with tempfile.TemporaryDirectory() as tmp:
        cache = ArtifactCache(root=tmp)
        cache.save(
            ScrapeResult(
                url="https://example.com",
                provider="raw_http",
                success=True,
                html="<html><body>HTML only</body></html>",
            )
        )

        assert cache.get_result("https://example.com", require_screenshot=True) is None


def test_save_does_not_pair_new_html_with_a_stale_screenshot():
    with tempfile.TemporaryDirectory() as tmp:
        cache = ArtifactCache(root=tmp)
        cache.save(
            ScrapeResult(
                url="https://example.com",
                provider="browserless",
                success=True,
                html="<html>old page</html>",
                screenshot=b"old-screenshot",
            )
        )
        cache.save(
            ScrapeResult(
                url="https://example.com",
                provider="raw_http",
                success=True,
                html="<html>new page</html>",
            )
        )

        assert cache.get_html("https://example.com") == "<html>new page</html>"
        assert cache.get_result("https://example.com", require_screenshot=True) is None


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
