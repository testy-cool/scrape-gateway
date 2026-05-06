import tempfile
from pathlib import Path

from scrape_gateway.config import GatewayConfig, _parse_ttl, load_config


def test_parse_ttl_seconds():
    assert _parse_ttl("30s") == 30


def test_parse_ttl_minutes():
    assert _parse_ttl("5m") == 300


def test_parse_ttl_hours():
    assert _parse_ttl("24h") == 86400


def test_parse_ttl_days():
    assert _parse_ttl("7d") == 604800


def test_parse_ttl_int():
    assert _parse_ttl(3600) == 3600


def test_parse_ttl_bare_string():
    assert _parse_ttl("3600") == 3600


def test_load_default_when_no_file():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = load_config(Path(tmp) / "nonexistent.yml")
    assert isinstance(cfg, GatewayConfig)
    assert cfg.providers == []
    assert cfg.cache.ttl_seconds == 86400


def test_load_yaml():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "scrape-gateway.yml"
        p.write_text(
            """
cache:
  ttl: 2h
  root: /tmp/test-cache

providers:
  - name: raw_http
  - name: scrapedrive
    enabled: true
    api_key_env: SCRAPEDRIVE_API_KEY
  - name: scraperapi
    enabled: false

strategy:
  mode: cheapest_successful
  max_cost_per_url: 0.05
"""
        )
        cfg = load_config(p)

    assert cfg.cache.ttl_seconds == 7200
    assert cfg.cache.root == "/tmp/test-cache"
    assert len(cfg.providers) == 3
    assert cfg.providers[0].name == "raw_http"
    assert cfg.providers[1].api_key_env == "SCRAPEDRIVE_API_KEY"
    assert cfg.providers[2].enabled is False
    assert cfg.strategy.max_cost_per_url == 0.05


def test_load_dotenv(monkeypatch, tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("TEST_KEY_XYZ=hello123\n")
    monkeypatch.delenv("TEST_KEY_XYZ", raising=False)

    import os

    from scrape_gateway.config import _load_dotenv

    _load_dotenv(dotenv)
    assert os.environ.get("TEST_KEY_XYZ") == "hello123"


def test_string_provider_shorthand():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "scrape-gateway.yml"
        p.write_text("providers:\n  - raw_http\n  - scrapedrive\n")
        cfg = load_config(p)
    assert cfg.providers[0].name == "raw_http"
    assert cfg.providers[1].name == "scrapedrive"
