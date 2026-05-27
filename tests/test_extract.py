"""Tests for sg extract: pattern detection, data extraction, field mapping, and memory."""

import tempfile
from pathlib import Path

import pytest

from scrape_gateway.cli import _detect_patterns, _element_to_row, _extract_rows, _apply_field_map
from scrape_gateway.memory import DomainMemory

# ── Realistic HTML fixtures ──────────────────────────────────────

PRODUCT_LISTING_HTML = """
<html><body>
<nav><ul>
  <li><a href="/">Home</a></li>
  <li><a href="/about">About</a></li>
  <li><a href="/contact">Contact</a></li>
  <li><a href="/blog">Blog</a></li>
</ul></nav>
<main>
<ol class="products">
  <li class="product-card">
    <div class="image-wrapper"><a href="/product/1"><img src="/img/1.jpg" alt="Widget A"></a></div>
    <h3><a href="/product/1">Widget A</a></h3>
    <span class="price-tag">$29.99</span>
    <span class="availability">In stock</span>
  </li>
  <li class="product-card">
    <div class="image-wrapper"><a href="/product/2"><img src="/img/2.jpg" alt="Widget B"></a></div>
    <h3><a href="/product/2">Widget B</a></h3>
    <span class="price-tag">$49.99</span>
    <span class="availability">In stock</span>
  </li>
  <li class="product-card">
    <div class="image-wrapper"><a href="/product/3"><img src="/img/3.jpg" alt="Widget C"></a></div>
    <h3><a href="/product/3">Widget C</a></h3>
    <span class="price-tag">$19.50</span>
    <span class="availability">Out of stock</span>
  </li>
  <li class="product-card">
    <div class="image-wrapper"><a href="/product/4"><img src="/img/4.jpg" alt="Widget D"></a></div>
    <h3><a href="/product/4">Widget D</a></h3>
    <span class="price-tag">$99.00</span>
    <span class="availability">In stock</span>
  </li>
</ol>
</main>
</body></html>
"""

ARTICLE_LISTING_HTML = """
<html><body>
<div class="blog-feed">
  <article class="post-card">
    <h2><a href="/post/hello-world">Hello World</a></h2>
    <time datetime="2026-05-01">May 1, 2026</time>
    <p class="excerpt">First post on the new blog.</p>
    <span class="author">Alice</span>
  </article>
  <article class="post-card">
    <h2><a href="/post/second-post">Second Post</a></h2>
    <time datetime="2026-05-05">May 5, 2026</time>
    <p class="excerpt">Follow-up thoughts on building things.</p>
    <span class="author">Bob</span>
  </article>
  <article class="post-card">
    <h2><a href="/post/third-post">Third Post</a></h2>
    <time datetime="2026-05-08">May 8, 2026</time>
    <p class="excerpt">Wrapping up the series.</p>
    <span class="author">Alice</span>
  </article>
</div>
</body></html>
"""

FLAT_LIST_HTML = """
<html><body>
<ul class="tags">
  <li><a href="/tag/python">Python</a></li>
  <li><a href="/tag/rust">Rust</a></li>
  <li><a href="/tag/go">Go</a></li>
</ul>
</body></html>
"""

NO_PATTERNS_HTML = """
<html><body><h1>Simple Page</h1><p>No repeated elements here.</p></body></html>
"""

DEEPLY_NESTED_HTML = """
<html><body>
<div class="results">
  <div class="result-item">
    <div class="result-header">
      <div class="result-title-wrap">
        <h3><a href="/r/1">Deep Result One</a></h3>
      </div>
    </div>
    <div class="result-body">
      <div class="result-meta"><span class="domain">example.com</span></div>
      <p class="snippet">A deeply nested search result.</p>
    </div>
  </div>
  <div class="result-item">
    <div class="result-header">
      <div class="result-title-wrap">
        <h3><a href="/r/2">Deep Result Two</a></h3>
      </div>
    </div>
    <div class="result-body">
      <div class="result-meta"><span class="domain">other.com</span></div>
      <p class="snippet">Another deeply nested result.</p>
    </div>
  </div>
  <div class="result-item">
    <div class="result-header">
      <div class="result-title-wrap">
        <h3><a href="/r/3">Deep Result Three</a></h3>
      </div>
    </div>
    <div class="result-body">
      <div class="result-meta"><span class="domain">third.com</span></div>
      <p class="snippet">Third result in the list.</p>
    </div>
  </div>
</div>
</body></html>
"""


# ── Pattern detection ────────────────────────────────────────────


class TestDetectPatterns:
    def test_finds_product_cards(self):
        patterns = _detect_patterns(PRODUCT_LISTING_HTML)
        repeated = patterns["repeated"]
        assert len(repeated) > 0, "should detect at least one repeated pattern"
        selectors = [f"{r['parent']} > {r['selector']}" for r in repeated]
        has_products = any("product-card" in s for s in selectors)
        assert has_products, f"should find product-card pattern, got: {selectors}"

    def test_finds_articles(self):
        patterns = _detect_patterns(ARTICLE_LISTING_HTML)
        repeated = patterns["repeated"]
        assert len(repeated) > 0, "should detect article pattern"
        has_articles = any("post-card" in r["selector"] for r in repeated)
        assert has_articles, f"should find post-card, got: {[r['selector'] for r in repeated]}"

    def test_finds_prices(self):
        patterns = _detect_patterns(PRODUCT_LISTING_HTML)
        assert "prices" in patterns, "should detect prices"
        assert len(patterns["prices"]) >= 3, (
            f"should find at least 3 prices, got {len(patterns['prices'])}"
        )

    def test_no_patterns_in_simple_page(self):
        patterns = _detect_patterns(NO_PATTERNS_HTML)
        assert len(patterns.get("repeated", [])) == 0, (
            "simple page should have no repeated patterns"
        )

    def test_sorted_by_count_descending(self):
        patterns = _detect_patterns(PRODUCT_LISTING_HTML)
        repeated = patterns["repeated"]
        counts = [r["count"] for r in repeated]
        assert counts == sorted(counts, reverse=True), (
            f"patterns should be sorted by count desc, got: {counts}"
        )

    def test_flat_list_detected(self):
        patterns = _detect_patterns(FLAT_LIST_HTML)
        repeated = patterns.get("repeated", [])
        assert len(repeated) > 0, "should detect flat list items"
        assert repeated[0]["count"] == 3, f"should find 3 items, got {repeated[0]['count']}"


# ── Element-to-row extraction ────────────────────────────────────


class TestElementToRow:
    def _parse_first(self, html, selector):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one(selector)
        assert el is not None, f"selector {selector!r} matched nothing"
        return _element_to_row(el)

    def test_product_card_extracts_all_fields(self):
        row = self._parse_first(PRODUCT_LISTING_HTML, "li.product-card")
        assert "title" in row, f"missing title, got keys: {list(row.keys())}"
        assert row["title"] == "Widget A", f"wrong title: {row['title']}"
        assert "href" in row, f"missing href, got keys: {list(row.keys())}"
        assert row["href"] == "/product/1", f"wrong href: {row['href']}"
        assert "image" in row, f"missing image, got keys: {list(row.keys())}"
        assert row["image"] == "/img/1.jpg", f"wrong image: {row['image']}"
        assert "price" in row, f"missing price, got keys: {list(row.keys())}"
        assert "$29.99" in row["price"], f"wrong price: {row['price']}"

    def test_article_extracts_date(self):
        row = self._parse_first(ARTICLE_LISTING_HTML, "article.post-card")
        assert "title" in row, f"missing title, got keys: {list(row.keys())}"
        assert row["title"] == "Hello World"
        assert "date" in row, f"missing date, got keys: {list(row.keys())}"
        assert "2026-05-01" in row["date"], f"wrong date: {row['date']}"

    def test_flat_link_item(self):
        row = self._parse_first(FLAT_LIST_HTML, "ul.tags > li")
        assert "title" in row or "text" in row, f"should have title or text, got: {row}"
        assert "href" in row, f"missing href: {row}"

    def test_deeply_nested_extracts_title(self):
        row = self._parse_first(DEEPLY_NESTED_HTML, "div.result-item")
        assert "title" in row, f"missing title from nested h3>a, got: {list(row.keys())}"
        assert "Deep Result One" in row["title"], f"wrong title: {row['title']}"
        assert "href" in row, f"missing href, got: {list(row.keys())}"

    def test_empty_element_returns_empty(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<div class='empty'></div>", "html.parser")
        row = _element_to_row(soup.select_one("div.empty"))
        assert row == {}, f"empty element should return empty dict, got: {row}"


# ── Full extraction pipeline ─────────────────────────────────────


class TestExtractRows:
    def test_products_with_selector(self):
        rows, desc = _extract_rows(PRODUCT_LISTING_HTML, selector="li.product-card")
        assert len(rows) == 4, f"expected 4 products, got {len(rows)}: {desc}"
        assert all("title" in r for r in rows), (
            f"all rows should have title: {[list(r.keys()) for r in rows]}"
        )
        assert all("price" in r for r in rows), (
            f"all rows should have price: {[list(r.keys()) for r in rows]}"
        )
        titles = [r["title"] for r in rows]
        assert titles == ["Widget A", "Widget B", "Widget C", "Widget D"], f"wrong titles: {titles}"

    def test_articles_with_selector(self):
        rows, desc = _extract_rows(ARTICLE_LISTING_HTML, selector="article.post-card")
        assert len(rows) == 3, f"expected 3 articles, got {len(rows)}: {desc}"
        assert all("title" in r for r in rows), "all rows should have title"
        assert all("date" in r for r in rows), "all rows should have date"

    def test_auto_detect_finds_something(self):
        rows, desc = _extract_rows(PRODUCT_LISTING_HTML)
        assert len(rows) > 0, f"auto-detect should find rows, got: {desc}"

    def test_pick_selects_correct_pattern(self):
        rows_1, desc_1 = _extract_rows(PRODUCT_LISTING_HTML, pick=1)
        rows_2, desc_2 = _extract_rows(PRODUCT_LISTING_HTML, pick=2)
        assert desc_1 != desc_2 or len(rows_1) != len(rows_2), (
            f"pick=1 and pick=2 should select different patterns: {desc_1} vs {desc_2}"
        )

    def test_no_patterns_returns_empty(self):
        rows, desc = _extract_rows(NO_PATTERNS_HTML)
        assert len(rows) == 0, f"should return empty for patternless page, got {len(rows)}: {desc}"
        assert "no repeated" in desc, f"desc should explain why empty: {desc}"

    def test_invalid_selector_returns_empty(self):
        rows, desc = _extract_rows(PRODUCT_LISTING_HTML, selector="div.nonexistent")
        assert len(rows) == 0, f"invalid selector should return empty, got {len(rows)}"
        assert "0 items" in desc, f"desc should show 0 items: {desc}"

    def test_deeply_nested_extraction(self):
        rows, desc = _extract_rows(DEEPLY_NESTED_HTML, selector="div.result-item")
        assert len(rows) == 3, f"expected 3 results, got {len(rows)}: {desc}"
        assert all("title" in r for r in rows), "all nested results should have title"
        assert all("href" in r for r in rows), "all nested results should have href"


# ── Field map application ────────────────────────────────────────


class TestFieldMap:
    def test_renames_fields(self):
        rows = [{"price-tag": "$29.99", "title": "Widget"}]
        mapped = _apply_field_map(rows, {"price-tag": "price"})
        assert "price" in mapped[0], f"should rename price-tag to price: {mapped[0]}"
        assert "price-tag" not in mapped[0], f"old key should be gone: {mapped[0]}"
        assert mapped[0]["title"] == "Widget", "unmapped fields should pass through"

    def test_empty_map_is_noop(self):
        rows = [{"a": "1", "b": "2"}]
        mapped = _apply_field_map(rows, {})
        assert mapped == rows, "empty map should return identical rows"

    def test_identity_map_is_noop(self):
        rows = [{"title": "X", "price": "$5"}]
        mapped = _apply_field_map(rows, {"title": "title", "price": "price"})
        assert mapped == rows, "identity map should return identical rows"


# ── Extraction memory (learn/recall) ─────────────────────────────


class TestExtractionMemory:
    def test_learn_and_recall(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
            mem.learn_extraction("shop.com", "ol > li.product", {"instock": "availability"})
            result = mem.get_extraction("shop.com")
            assert result is not None, "should recall learned pattern"
            selector, field_map = result
            assert selector == "ol > li.product", f"wrong selector: {selector}"
            assert field_map == {"instock": "availability"}, f"wrong field_map: {field_map}"

    def test_no_memory_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
            assert mem.get_extraction("unknown.com") is None, "unknown domain should return None"

    def test_overwrite_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
            mem.learn_extraction("shop.com", "ol > li.v1", {})
            mem.learn_extraction("shop.com", "ol > li.v2", {"a": "b"})
            selector, field_map = mem.get_extraction("shop.com")
            assert selector == "ol > li.v2", f"should use latest selector: {selector}"
            assert field_map == {"a": "b"}, f"should use latest field_map: {field_map}"


# ── Live integration tests (hit real URLs, cost money) ───────────


@pytest.mark.live
class TestExtractLive:
    """These tests hit real URLs. Run with: pytest -m live"""

    @pytest.fixture
    def gateway(self):
        from scrape_gateway.router import ScrapeGateway

        return ScrapeGateway.from_config()

    @pytest.mark.asyncio
    async def test_books_toscrape_products(self, gateway):
        """books.toscrape.com product listing — the canonical test."""
        from scrape_gateway.models import ScrapeRequest

        result = await gateway.scrape(ScrapeRequest("https://books.toscrape.com"))
        assert result.success, f"scrape failed: {result.error or result.failure_reason}"
        assert result.html, "scrape returned empty HTML"

        rows, desc = _extract_rows(result.html, selector="ol.row > li")
        assert len(rows) == 20, f"expected 20 books, got {len(rows)}: {desc}"

        first = rows[0]
        assert "title" in first, f"first book missing title, got: {list(first.keys())}"
        assert "price" in first, f"first book missing price, got: {list(first.keys())}"
        assert "image" in first, f"first book missing image, got: {list(first.keys())}"
        assert "href" in first, f"first book missing href, got: {list(first.keys())}"
        assert first["price"].startswith("£"), f"price should start with £: {first['price']}"

    @pytest.mark.asyncio
    async def test_auto_detect_picks_content(self, gateway):
        """Auto-detect on books.toscrape.com should find some pattern."""
        from scrape_gateway.models import ScrapeRequest

        result = await gateway.scrape(ScrapeRequest("https://books.toscrape.com"))
        assert result.success, f"scrape failed: {result.error or result.failure_reason}"

        patterns = _detect_patterns(result.html)
        repeated = patterns.get("repeated", [])
        assert len(repeated) >= 2, f"should find at least 2 patterns, got {len(repeated)}"

        rows, desc = _extract_rows(result.html)
        assert len(rows) > 0, f"auto-detect should extract rows: {desc}"

    @pytest.mark.asyncio
    async def test_example_com_no_listings(self, gateway):
        """example.com has no repeated patterns — extract should return empty."""
        from scrape_gateway.models import ScrapeRequest

        result = await gateway.scrape(ScrapeRequest("https://example.com"))
        assert result.success, f"scrape failed: {result.error or result.failure_reason}"

        rows, desc = _extract_rows(result.html)
        assert len(rows) == 0, (
            f"example.com should have no extractable listings, got {len(rows)}: {desc}"
        )
