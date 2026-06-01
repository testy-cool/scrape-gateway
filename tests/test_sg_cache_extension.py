from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path


def _load_sg_cache():
    path = Path(__file__).resolve().parents[1] / "extensions/sg-cache/src/sg_cache/__init__.py"
    spec = importlib.util.spec_from_file_location("sg_cache_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_entry(root: Path, key: str, *, url: str, fetched_at: float) -> Path:
    folder = root / key
    folder.mkdir()
    (folder / "page.html").write_text("<html>hello</html>")
    (folder / "page.md").write_text("hello")
    (folder / "meta.json").write_text(
        json.dumps(
            {
                "url": url,
                "provider": "raw_http",
                "route": "raw_http",
                "fetched_at": fetched_at,
            }
        )
    )
    return folder


def test_cache_extension_iterates_entries(tmp_path):
    sg_cache = _load_sg_cache()
    _write_entry(tmp_path, "fresh", url="https://example.com/page", fetched_at=time.time())
    _write_entry(tmp_path, "old", url="https://old.example.com/", fetched_at=1)

    entries = sg_cache._iter_entries(tmp_path, ttl_seconds=60)

    assert {entry.key for entry in entries} == {"fresh", "old"}
    assert next(entry for entry in entries if entry.key == "fresh").expired is False
    assert next(entry for entry in entries if entry.key == "old").expired is True


def test_cache_extension_filters_by_domain_and_expiration(tmp_path):
    sg_cache = _load_sg_cache()
    _write_entry(tmp_path, "a", url="https://example.com/page", fetched_at=time.time())
    _write_entry(tmp_path, "b", url="https://sub.example.com/page", fetched_at=1)
    _write_entry(tmp_path, "c", url="https://other.test/page", fetched_at=time.time())

    entries = sg_cache._iter_entries(tmp_path, ttl_seconds=60)
    domain_entries = sg_cache._select_entries(entries, domain="example.com")
    expired_entries = sg_cache._select_entries(entries, expired_only=True)

    assert {entry.key for entry in domain_entries} == {"a", "b"}
    assert {entry.key for entry in expired_entries} == {"b"}
