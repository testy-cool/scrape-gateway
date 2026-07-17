import tempfile
from pathlib import Path

import pytest

from scrape_gateway.config import (
    EvaluationConfig,
    GatewayConfig,
    _parse_ttl,
    load_config,
    save_operator_settings,
)


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

telemetry:
  enabled: true
  root: /tmp/test-runs
  debug_artifacts: true

evaluation:
  mode: audit
  model: google/gemini-3.1-flash-lite
  max_markdown_chars: 25000
  include_screenshot: true
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
    assert cfg.telemetry.root == "/tmp/test-runs"
    assert cfg.telemetry.debug_artifacts is True
    assert cfg.evaluation.mode == "audit"
    assert cfg.evaluation.model == "google/gemini-3.1-flash-lite"
    assert cfg.evaluation.max_markdown_chars == 25000
    assert cfg.evaluation.include_screenshot is True


def test_operator_settings_override_provider_and_timeout_defaults(tmp_path):
    config_path = tmp_path / "scrape-gateway.yml"
    config_path.write_text(
        """
providers:
  - name: raw_http
    enabled: true
  - name: scrapedrive
    enabled: true
evaluation:
  mode: audit
"""
    )

    settings_path = save_operator_settings(
        {
            "default_timeout_seconds": 32,
            "evaluation_timeout_seconds": 75,
            "providers": [
                {"name": "raw_http", "enabled": True, "timeout_seconds": 8},
                {"name": "scrapedrive", "enabled": False, "timeout_seconds": 19},
            ],
        },
        config_path=config_path,
    )

    assert settings_path == tmp_path / ".scrape-gateway" / "operator-settings.yml"
    cfg = load_config(config_path)
    assert cfg.request.default_timeout_seconds == 32
    assert cfg.evaluation.timeout_seconds == 75
    assert cfg.providers[0].timeout_seconds == 8
    assert cfg.providers[1].enabled is False
    assert cfg.providers[1].timeout_seconds == 19


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"mode": "gate"}, "mode must be 'off' or 'audit'"),
        ({"max_markdown_chars": 0}, "max_markdown_chars must be positive"),
    ],
)
def test_invalid_evaluation_config_fails_early(kwargs, message):
    with pytest.raises(ValueError, match=message):
        EvaluationConfig(**kwargs)


def test_unquoted_yaml_off_disables_evaluation(tmp_path):
    config_path = tmp_path / "scrape-gateway.yml"
    config_path.write_text("evaluation:\n  mode: off\n")

    config = load_config(config_path)

    assert config.evaluation.mode == "off"


def test_load_dotenv(monkeypatch, tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("TEST_KEY_XYZ=hello123\n")
    monkeypatch.delenv("TEST_KEY_XYZ", raising=False)

    import os

    from scrape_gateway.config import _load_dotenv

    _load_dotenv(dotenv)
    assert os.environ.get("TEST_KEY_XYZ") == "hello123"


def test_load_dotenv_does_not_override_existing_env(monkeypatch, tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("TEST_KEY_XYZ=from_dotenv\n")
    monkeypatch.setenv("TEST_KEY_XYZ", "from_shell")

    import os

    from scrape_gateway.config import _load_dotenv

    _load_dotenv(dotenv)
    assert os.environ.get("TEST_KEY_XYZ") == "from_shell"


def test_string_provider_shorthand():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "scrape-gateway.yml"
        p.write_text("providers:\n  - raw_http\n  - scrapedrive\n")
        cfg = load_config(p)
    assert cfg.providers[0].name == "raw_http"
    assert cfg.providers[1].name == "scrapedrive"
