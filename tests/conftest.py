"""Pytest configuration: custom HTML report with plain-language test descriptions."""

import pytest

# ── Human-readable descriptions for every test ──────────────────
# Maps test node IDs to (description, why_it_matters) tuples.

TEST_DOCS = {
    # -- Cache --
    "test_cache.py::test_save_and_get_html": (
        "Saves a scraped page and retrieves it by URL",
        "Core cache contract: what goes in must come back out identical.",
    ),
    "test_cache.py::test_cache_miss": (
        "Returns None for a URL that was never cached",
        "Prevents stale data from leaking — a miss must be clearly distinguishable from a hit.",
    ),
    "test_cache.py::test_saves_markdown": (
        "Auto-converts HTML to Markdown when saving",
        "Markdown is lighter than HTML for downstream LLM consumption. Verifies the conversion runs on save.",
    ),
    "test_cache.py::test_saves_meta_json": (
        "Stores metadata (provider, timestamp) alongside the HTML",
        "Lets us know where the data came from and when — needed for TTL expiry and debugging.",
    ),
    "test_cache.py::test_ttl_expired": (
        "Treats cached data as missing after its TTL expires",
        "Stale data is worse than no data. TTL ensures we re-scrape after the configured freshness window.",
    ),
    "test_cache.py::test_ttl_not_expired": (
        "Returns cached data when still within TTL",
        "Avoids unnecessary re-scrapes and API costs when data is still fresh.",
    ),
    # -- Config --
    "test_config.py::test_parse_ttl_seconds": (
        "Parses '30s' as 30 seconds",
        "Human-friendly time strings ('30s', '5m', '24h') must map to the correct number of seconds.",
    ),
    "test_config.py::test_parse_ttl_minutes": (
        "Parses '5m' as 300 seconds",
        "Same — verifying minutes conversion.",
    ),
    "test_config.py::test_parse_ttl_hours": (
        "Parses '24h' as 86400 seconds",
        "Same — verifying hours conversion.",
    ),
    "test_config.py::test_parse_ttl_days": (
        "Parses '7d' as 604800 seconds",
        "Same — verifying days conversion.",
    ),
    "test_config.py::test_parse_ttl_int": (
        "Passes through a raw integer unchanged",
        "If the user writes a bare number, treat it as seconds directly.",
    ),
    "test_config.py::test_parse_ttl_bare_string": (
        "Parses a bare string number ('3600') as seconds",
        "Edge case: a number passed as a string (common in YAML) should still work.",
    ),
    "test_config.py::test_load_default_when_no_file": (
        "Uses sensible defaults when no config file exists",
        "sg should work out of the box with zero configuration.",
    ),
    "test_config.py::test_load_yaml": (
        "Loads a full YAML config file with all sections",
        "Verifies that cache, providers, and strategy sections all parse correctly.",
    ),
    "test_config.py::test_load_dotenv": (
        "Loads API keys from a .env file",
        "Users put secrets in .env — this ensures they're available as environment variables.",
    ),
    "test_config.py::test_string_provider_shorthand": (
        "Accepts provider names as plain strings instead of objects",
        "Shorthand: 'providers: [raw_http, scrapedrive]' instead of verbose objects.",
    ),
    # -- Errors --
    "test_errors.py::test_403": (
        "Classifies HTTP 403 as a 'forbidden' failure",
        "Different HTTP errors need different handling. 403 means the site is blocking us.",
    ),
    "test_errors.py::test_429": (
        "Classifies HTTP 429 as 'rate limited'",
        "429 means slow down — the router should wait or switch providers.",
    ),
    "test_errors.py::test_5xx": (
        "Classifies HTTP 500/503 as 'server error'",
        "Server-side failures — worth retrying with the same provider.",
    ),
    "test_errors.py::test_empty_content": (
        "Classifies a blank or whitespace-only response as 'empty'",
        "Some sites return 200 OK with no body — that's a failure, not a success.",
    ),
    "test_errors.py::test_captcha": (
        "Detects captcha challenge pages in the response body",
        "The HTTP status is 200 but the page is a captcha wall — we need to catch this.",
    ),
    "test_errors.py::test_cloudflare": (
        "Detects Cloudflare 'checking your browser' pages",
        "Same — 200 status but the content is a Cloudflare challenge, not real data.",
    ),
    "test_errors.py::test_js_required": (
        "Detects pages that need JavaScript to render",
        "If we scraped without JS and got a 'please enable JavaScript' page, we need to retry with rendering.",
    ),
    "test_errors.py::test_clean_page": (
        "Accepts a normal page with real content as valid",
        "Sanity check: real pages must NOT be classified as errors.",
    ),
    # -- Extract --
    "test_extract.py::TestDetectPatterns::test_finds_product_cards": (
        "Detects repeated product cards in a product listing page",
        "The foundation of sg extract: can we find the repeated elements that form a listing?",
    ),
    "test_extract.py::TestDetectPatterns::test_finds_articles": (
        "Detects repeated article cards in a blog feed",
        "Same detection, different HTML structure — articles instead of products.",
    ),
    "test_extract.py::TestDetectPatterns::test_finds_prices": (
        "Spots price-like values ($29.99) in the page",
        "Price detection is a common extraction need. Tests the heuristic pattern matcher.",
    ),
    "test_extract.py::TestDetectPatterns::test_no_patterns_in_simple_page": (
        "Returns zero patterns for a page with no repeated elements",
        "A page with just a heading and paragraph should produce no false positives.",
    ),
    "test_extract.py::TestDetectPatterns::test_sorted_by_count_descending": (
        "Lists detected patterns with the most repeated first",
        "The default pick is the highest-count pattern, so sorting must be correct.",
    ),
    "test_extract.py::TestDetectPatterns::test_flat_list_detected": (
        "Detects a flat <ul>/<li> list as a repeated pattern",
        "Not just complex cards — simple lists should also be detected.",
    ),
    "test_extract.py::TestElementToRow::test_product_card_extracts_all_fields": (
        "Extracts title, price, image, and link from a product card",
        "The core value: given one card element, pull out all its structured data.",
    ),
    "test_extract.py::TestElementToRow::test_article_extracts_date": (
        "Extracts the publication date from an article card",
        "Dates in <time> elements should be captured — common in blog/news feeds.",
    ),
    "test_extract.py::TestElementToRow::test_flat_link_item": (
        "Extracts text and href from a simple list item",
        "Even basic <li><a> items should produce usable rows.",
    ),
    "test_extract.py::TestElementToRow::test_deeply_nested_extracts_title": (
        "Finds title and link buried 4 levels deep in nested divs",
        "Real-world HTML nests data deep. Extraction must search the full subtree, not just direct children.",
    ),
    "test_extract.py::TestElementToRow::test_empty_element_returns_empty": (
        "Returns an empty dict for an element with no extractable content",
        "Don't crash or fabricate data when an element is genuinely empty.",
    ),
    "test_extract.py::TestExtractRows::test_products_with_selector": (
        "Extracts all 4 products when given the correct CSS selector",
        "Happy path: explicit selector, correct data, all rows.",
    ),
    "test_extract.py::TestExtractRows::test_articles_with_selector": (
        "Extracts all 3 articles with their dates",
        "Same pipeline, different content type — verifies generality.",
    ),
    "test_extract.py::TestExtractRows::test_auto_detect_finds_something": (
        "Auto-detect (no selector given) still extracts rows from a product page",
        "The LLM-free fallback must find something reasonable without manual hints.",
    ),
    "test_extract.py::TestExtractRows::test_pick_selects_correct_pattern": (
        "--pick 1 and --pick 2 select different patterns",
        "When a page has multiple repeated patterns (nav + products), pick lets you choose.",
    ),
    "test_extract.py::TestExtractRows::test_no_patterns_returns_empty": (
        "Returns empty rows and an explanation for a patternless page",
        "Agent-friendly: don't error, explain why there's nothing to extract.",
    ),
    "test_extract.py::TestExtractRows::test_invalid_selector_returns_empty": (
        "Returns empty rows when the CSS selector matches nothing",
        "Typos happen. Show '0 items matched' instead of crashing.",
    ),
    "test_extract.py::TestExtractRows::test_deeply_nested_extraction": (
        "Extracts data from elements nested 4+ levels deep",
        "The full pipeline (detect → extract) working on deeply nested HTML.",
    ),
    "test_extract.py::TestFieldMap::test_renames_fields": (
        "Renames 'price-tag' to 'price' using a field map",
        "The LLM suggests better names (CSS class → semantic label). This applies them.",
    ),
    "test_extract.py::TestFieldMap::test_empty_map_is_noop": (
        "An empty field map leaves rows unchanged",
        "When the LLM has no suggestions, data passes through unmodified.",
    ),
    "test_extract.py::TestFieldMap::test_identity_map_is_noop": (
        "A field map that maps names to themselves is a no-op",
        "Edge case: LLM says 'title → title'. Should not duplicate or drop fields.",
    ),
    "test_extract.py::TestExtractionMemory::test_learn_and_recall": (
        "Remembers a selector and field map for a domain, then recalls them",
        "First scrape uses LLM. Every repeat loads from memory — $0 cost.",
    ),
    "test_extract.py::TestExtractionMemory::test_no_memory_returns_none": (
        "Returns None for a domain with no learned pattern",
        "Distinguishes 'never seen' from 'seen and learned'.",
    ),
    "test_extract.py::TestExtractionMemory::test_overwrite_updates": (
        "A second learn() for the same domain overwrites the first",
        "Site layouts change. The latest pattern should win.",
    ),
    # -- Memory --
    "test_memory.py::test_remember_and_recall": (
        "Remembers which provider succeeded for a domain and recalls it",
        "The core of domain memory: skip trial-and-error on repeat visits.",
    ),
    "test_memory.py::test_no_memory": (
        "Returns None for a domain never scraped before",
        "First visit to a domain should try all providers, not assume anything.",
    ),
    "test_memory.py::test_domain_extraction": (
        "Extracts 'example.com' from 'https://www.example.com/path'",
        "Memory is per-domain, not per-URL. This tests the domain normalization.",
    ),
    "test_memory.py::test_success_count_increments": (
        "Tracks how many times a provider succeeded for a domain",
        "More successes = higher confidence. Used to rank providers.",
    ),
    "test_memory.py::test_remember_failure": (
        "Tracks provider failures separately from successes",
        "Both sides matter: success rate = successes / (successes + failures).",
    ),
    "test_memory.py::test_remember_block": (
        "Records the type of block (e.g. Cloudflare) when a provider is blocked",
        "Blocks are worse than failures — the site is actively fighting us.",
    ),
    "test_memory.py::test_should_skip_after_repeated_failures": (
        "Skips a provider after 5 consecutive failures on a domain",
        "Don't keep wasting money on a provider that consistently fails for a site.",
    ),
    "test_memory.py::test_should_not_skip_with_no_history": (
        "Doesn't skip a provider that has never been tried",
        "No history means no opinion — give it a chance.",
    ),
    "test_memory.py::test_should_not_skip_with_good_success_rate": (
        "Keeps using a provider with 10 successes and 1 failure",
        "One failure shouldn't blacklist a provider that usually works.",
    ),
    "test_memory.py::test_prefers_provider_with_better_record": (
        "Prefers the provider with 5 successes over one with 1 success",
        "More evidence of success = higher priority in the routing order.",
    ),
    "test_memory.py::test_blocks_penalized_harder": (
        "A Cloudflare block counts 3x worse than a normal failure",
        "Blocks indicate active anti-bot measures. Much less likely to work next time.",
    ),
    "test_memory.py::test_preferred_provider_returns_tier": (
        "Remembers which ScrapeDrive tier worked (e.g. 'advanced')",
        "If standard tier failed but advanced worked, start at advanced next time — saves one failed request.",
    ),
    "test_memory.py::test_preferred_provider_returns_none_tuple_when_no_tier": (
        "Returns (provider, None) when no tier info is stored",
        "Non-ScrapeDrive providers don't have tiers. Memory handles this gracefully.",
    ),
    "test_memory.py::test_preferred_provider_returns_none_when_no_history": (
        "Returns None (not a tuple) for unknown domains",
        "Callers check 'if result is None' vs 'result[0]'. The return types must be distinct.",
    ),
    "test_memory.py::test_stores_tier_info": (
        "Persists the tier and country alongside the provider name",
        "Full context: which provider, which tier, which country — all saved for replay.",
    ),
    # -- Providers --
    "test_providers.py::TestRawHttp::test_success": (
        "Raw HTTP provider returns HTML on a 200 response",
        "The free, no-API-key provider. Just a direct HTTP GET.",
    ),
    "test_providers.py::TestRawHttp::test_timeout": (
        "Raw HTTP correctly reports a timeout",
        "Network timeouts must be caught and classified, not crash the whole router.",
    ),
    "test_providers.py::TestRawHttp::test_403": (
        "Raw HTTP classifies a 403 response as forbidden",
        "The router needs to know WHY it failed to decide what to try next.",
    ),
    "test_providers.py::TestScrapeDrive::test_missing_api_key": (
        "ScrapeDrive returns a clear error when API key is missing",
        "Better to fail early with a helpful message than make a keyless API call.",
    ),
    "test_providers.py::TestScrapeDrive::test_standard_tier": (
        "ScrapeDrive sends correct API params for standard tier",
        "Verifies the request URL, API key, tier param, and cost tracking.",
    ),
    "test_providers.py::TestScrapeDrive::test_premium_maps_to_hyperdrive": (
        "The 'premium' flag maps to ScrapeDrive's 'hyperdrive' tier",
        "Abstraction layer: sg uses 'premium', ScrapeDrive calls it 'hyperdrive'.",
    ),
    "test_providers.py::TestScrapeDrive::test_country_maps_to_advanced": (
        "Requesting a country auto-upgrades to 'advanced' tier",
        "Country-specific scraping requires geo-proxies — only available on advanced+.",
    ),
    "test_providers.py::TestScrapeDrive::test_timeout": (
        "ScrapeDrive correctly reports a timeout",
        "Same timeout handling pattern as raw HTTP — consistency across providers.",
    ),
    "test_providers.py::TestScrapeDrive::test_json_response": (
        "ScrapeDrive parses JSON responses with separate html/markdown fields",
        "ScrapeDrive can return structured JSON instead of raw HTML.",
    ),
    "test_providers.py::TestScrapeDrive::test_respects_start_tier": (
        "ScrapeDrive starts at the specified tier when told to",
        "Tier escalation: domain memory says 'start at advanced', ScrapeDrive obeys.",
    ),
    "test_providers.py::TestScrapeDrive::test_start_tier_hyperdrive": (
        "ScrapeDrive correctly starts at hyperdrive when specified",
        "Same as above, testing the highest tier.",
    ),
    "test_providers.py::TestScrapeDrive::test_ignores_irrelevant_start_tier": (
        "ScrapeDrive ignores a start_tier meant for another provider",
        "'scraperapi:premium' is not a ScrapeDrive tier — should fall back to standard.",
    ),
    "test_providers.py::TestScrapeDo::test_missing_token": (
        "Scrape.do returns a clear error when token is missing",
        "Same pattern as ScrapeDrive: fail early with a message.",
    ),
    "test_providers.py::TestScrapeDo::test_success": (
        "Scrape.do sends correct params and returns HTML",
        "Verifies token and URL are passed correctly in the API call.",
    ),
    "test_providers.py::TestScrapeDo::test_params_country_premium_render": (
        "Scrape.do maps country, premium, and render_js to its API params",
        "Each provider has its own param names. sg's abstraction must translate correctly.",
    ),
    "test_providers.py::TestScrapeDo::test_timeout": (
        "Scrape.do correctly reports a timeout",
        "Consistent timeout handling.",
    ),
    "test_providers.py::TestScrapingBee::test_missing_api_key": (
        "ScrapingBee returns a clear error when API key is missing",
        "Same pattern.",
    ),
    "test_providers.py::TestScrapingBee::test_success": (
        "ScrapingBee sends correct params and returns HTML",
        "Verifies API key, URL, and render_js=false by default.",
    ),
    "test_providers.py::TestScrapingBee::test_params_country_premium": (
        "ScrapingBee maps country and premium to its API params",
        "ScrapingBee uses 'premium_proxy', 'country_code' — different from other providers.",
    ),
    "test_providers.py::TestScrapingBee::test_timeout": (
        "ScrapingBee correctly reports a timeout",
        "Consistent timeout handling.",
    ),
    "test_providers.py::TestScraperApi::test_missing_api_key": (
        "ScraperAPI returns a clear error when API key is missing",
        "Same pattern.",
    ),
    "test_providers.py::TestScraperApi::test_success": (
        "ScraperAPI sends correct params and returns HTML",
        "Verifies the ScraperAPI-specific URL format and params.",
    ),
    "test_providers.py::TestScraperApi::test_params": (
        "ScraperAPI maps country, premium, and render to its API params",
        "ScraperAPI uses 'premium=true', 'render=true' — its own naming convention.",
    ),
    "test_providers.py::TestScraperApi::test_screenshot_response": (
        "ScraperAPI returns screenshot bytes when requested",
        "Screenshots come back as PNG binary, not HTML. Provider must handle both.",
    ),
    "test_providers.py::TestScraperApi::test_timeout": (
        "ScraperAPI correctly reports a timeout",
        "Consistent timeout handling.",
    ),
    # -- Router --
    "test_router.py::test_routes_to_first_success": (
        "Tries providers in order, uses the first one that works",
        "The core routing algorithm: fail → fail → success → stop.",
    ),
    "test_router.py::test_returns_last_failure_when_all_fail": (
        "Returns the last failure result when every provider fails",
        "When nothing works, the caller gets the last error — not a silent empty result.",
    ),
    "test_router.py::test_cache_hit": (
        "Returns cached data without calling any provider",
        "Cache hit = zero API calls = zero cost. The fastest and cheapest path.",
    ),
    "test_router.py::test_remembers_successful_provider": (
        "After a success, the domain memory stores which provider worked",
        "Next time we scrape this domain, we skip straight to the winner.",
    ),
    "test_router.py::test_preferred_provider_tried_first": (
        "The remembered provider is tried before cheaper alternatives",
        "Domain memory overrides cost-based ordering. A $0.05 provider that works beats a free one that doesn't.",
    ),
    "test_router.py::test_validator_rejects_block_page_and_escalates": (
        "A Cloudflare page (200 OK but blocked) is rejected and the next provider is tried",
        "Content validation: status 200 doesn't mean success. The HTML itself might be a block page.",
    ),
    "test_router.py::test_validator_marks_block_type": (
        "When all providers return block pages, the result includes the block type",
        "Diagnostics: the caller knows it's 'cloudflare' vs 'captcha' vs 'js_shell'.",
    ),
    "test_router.py::test_no_providers_returns_error": (
        "With zero providers configured, returns a clear error",
        "Edge case: misconfiguration shouldn't crash, it should explain the problem.",
    ),
    "test_router.py::test_skips_providers_cheaper_than_preferred": (
        "Once a domain needs an expensive provider, cheap ones are skipped entirely",
        "If ScrapeDrive advanced is needed, don't waste time trying free providers first.",
    ),
    "test_router.py::test_tier_escalation_full_flow": (
        "Full flow: free provider blocked → ScrapeDrive succeeds → next scrape skips free and starts at the right tier",
        "End-to-end: the router learns from experience and gets faster/cheaper over time.",
    ),
    # -- Validators --
    "test_validators.py::test_valid_page": (
        "Accepts a normal HTML page with real content",
        "Sanity check: real pages must pass validation.",
    ),
    "test_validators.py::test_empty_content": (
        "Rejects empty or whitespace-only HTML",
        "A 200 OK with no body is a failure, not a success.",
    ),
    "test_validators.py::test_short_content": (
        "Rejects HTML with too little text content",
        "Tiny pages (<50 chars of text) are usually errors or placeholders.",
    ),
    "test_validators.py::test_cloudflare_block": (
        "Detects Cloudflare 'checking your browser' challenge pages",
        "The most common anti-bot block. Must be caught despite the 200 status.",
    ),
    "test_validators.py::test_captcha_block": (
        "Detects captcha/reCAPTCHA challenge pages",
        "Another common block type that returns 200 but isn't real content.",
    ),
    "test_validators.py::test_js_required": (
        "Detects 'enable JavaScript' placeholder pages",
        "Signals that we need to re-scrape with JS rendering enabled.",
    ),
    "test_validators.py::test_must_not_contain": (
        "Rejects pages containing forbidden phrases",
        "Custom validation: 'if the page says X, it's not what we wanted'.",
    ),
    "test_validators.py::test_must_contain_any_passes": (
        "Accepts pages containing at least one required phrase",
        "Custom validation: 'the page must mention reviews OR pricing'.",
    ),
    "test_validators.py::test_must_contain_any_fails": (
        "Rejects pages missing all required phrases",
        "Flip side: if none of the expected keywords appear, it's the wrong page.",
    ),
    "test_validators.py::test_custom_min_chars": (
        "Allows overriding the minimum character threshold",
        "Some pages are legitimately tiny. The caller can lower the bar.",
    ),
    "test_validators.py::test_none_input": (
        "Rejects None as input (treats it as empty)",
        "Defensive: a provider returning None instead of a string shouldn't crash validation.",
    ),
}

# Category descriptions for the HTML report
CATEGORY_DOCS = {
    "test_cache": (
        "Cache",
        "Scraping costs money and time. The cache stores results locally so repeat visits "
        "to the same URL are instant and free. These tests verify save, retrieve, TTL expiry, "
        "and metadata storage.",
    ),
    "test_config": (
        "Configuration",
        "sg reads settings from a YAML file and .env for secrets. These tests verify "
        "that time strings parse correctly, defaults work when no config exists, and "
        "all config sections load properly.",
    ),
    "test_errors": (
        "Error Classification",
        "When a scrape fails, we need to know WHY — is it a 403 block, a rate limit, "
        "an empty page, or a captcha? The error classifier inspects HTTP status codes "
        "and response bodies to categorize failures. The router uses this to decide "
        "what to try next.",
    ),
    "test_extract": (
        "Data Extraction",
        "The sg extract command: detect repeated patterns on a page (product cards, "
        "article lists), pull structured data from each element, and optionally use an "
        "LLM to pick the best pattern and name the fields. These tests cover the full "
        "pipeline from HTML to JSON rows.",
    ),
    "test_memory": (
        "Domain Memory",
        "sg remembers which scraping provider worked for each domain. After one successful "
        "scrape of example.com with ScrapeDrive, every future scrape skips straight to "
        "ScrapeDrive instead of trying free providers first. Memory also tracks failures, "
        "blocks, and tier escalation.",
    ),
    "test_providers": (
        "Provider Adapters",
        "Each scraping provider (raw HTTP, ScrapeDrive, ScrapingBee, ScraperAPI, Scrape.do) "
        "has a different API with different parameter names and response formats. These "
        "adapters normalize everything into a common ScrapeRequest/ScrapeResult interface. "
        "Tests use mocked HTTP to verify correct API calls without spending money.",
    ),
    "test_router": (
        "Router & Strategy",
        "The router is the brain: it decides which provider to try, in what order, and "
        "handles fallback when one fails. It checks the cache first, consults domain "
        "memory, validates returned content (catching block pages), and remembers what "
        "worked. These tests simulate multi-provider scenarios end-to-end.",
    ),
    "test_validators": (
        "Content Validation",
        "A scrape can return HTTP 200 but still fail — the page might be a Cloudflare "
        "challenge, a captcha wall, or a 'please enable JavaScript' placeholder. The "
        "validator inspects the actual HTML content to catch these. It also supports "
        "custom rules like 'page must contain the word pricing'.",
    ),
}


def _nodeid_to_key(nodeid: str) -> str:
    """Convert 'tests/test_foo.py::TestBar::test_baz' to 'test_foo.py::TestBar::test_baz'."""
    return nodeid.split("tests/")[-1] if "tests/" in nodeid else nodeid


def _nodeid_to_category(nodeid: str) -> str:
    """Extract 'test_foo' from 'tests/test_foo.py::...'."""
    filename = nodeid.split("/")[-1].split("::")[0]
    return filename.replace(".py", "")


def _name_to_description(name: str) -> str:
    """Fallback: convert test_some_behavior to 'Some behavior'."""
    name = name.replace("test_", "", 1)
    name = name.replace("_", " ")
    return name.capitalize()


# ── pytest-html hooks ───────────────────────────────────────────


def pytest_html_results_table_header(cells):
    cells.insert(1, '<th class="sortable">Description</th>')
    cells.insert(2, "<th>Why It Matters</th>")


def pytest_html_results_table_row(report, cells):
    key = _nodeid_to_key(report.nodeid)
    if key in TEST_DOCS:
        desc, why = TEST_DOCS[key]
    else:
        desc = _name_to_description(report.nodeid.split("::")[-1])
        why = ""
    cells.insert(1, f"<td>{desc}</td>")
    cells.insert(2, f"<td>{why}</td>")


@pytest.fixture(autouse=True)
def _doc_metadata(request):
    """Attach description to the test item for pytest-html 'Description' column."""
    key = _nodeid_to_key(request.node.nodeid)
    if key in TEST_DOCS:
        desc, _ = TEST_DOCS[key]
        request.node.user_properties.append(("description", desc))
