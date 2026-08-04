from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

CORPUS_ROOT = Path(__file__).parent / "fixtures" / "evaluator_calibration" / "v1"
CASES_PATH = CORPUS_ROOT / "cases.json"
REQUIRED_FIELDS = {
    "id",
    "classification",
    "source_url",
    "captured_at",
    "content_chars",
    "excerpt",
    "artifact",
    "capture_provider",
    "capture_cost_units",
    "status_code",
    "evaluation_goal",
    "country",
    "human_verdict",
    "human_root_cause",
    "human_issue_codes",
    "label_notes",
    "score",
    "split",
    "content_sha256",
}
REQUIRED_CLASSIFICATIONS = {
    "clean_article",
    "clean_product",
    "clean_listing",
    "content_cookie_mention",
    "content_adversarial_terms",
    "bot_block",
    "captcha_wall",
    "login_wall",
    "paywall",
    "cookie_wall",
    "js_shell",
    "truncated",
    "wrong_locale",
    "empty_response",
}


def _load_cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def test_calibration_corpus_is_balanced_and_covers_required_categories() -> None:
    cases = _load_cases()

    assert len(cases) == 60
    assert Counter(case["human_verdict"] for case in cases) == {"pass": 30, "fail": 30}
    classifications = Counter(case["classification"] for case in cases)
    assert set(classifications) == REQUIRED_CLASSIFICATIONS
    assert min(classifications.values()) >= 3

    split_verdicts: dict[str, Counter] = defaultdict(Counter)
    for case in cases:
        split_verdicts[case["split"]][case["human_verdict"]] += 1
    assert set(split_verdicts) == {"train", "dev", "test"}
    for counts in split_verdicts.values():
        assert abs(counts["pass"] - counts["fail"]) <= 1


def test_calibration_corpus_records_auditable_free_provider_captures() -> None:
    cases = _load_cases()
    ids: set[str] = set()

    for case in cases:
        assert REQUIRED_FIELDS <= case.keys()
        assert case["id"] not in ids
        ids.add(case["id"])
        assert case["capture_provider"] in {"raw_http", "curl_cffi"}
        assert case["capture_cost_units"] == 0
        assert case["score"] is True
        assert case["human_verdict"] in {"pass", "fail"}
        assert case["human_root_cause"] in {
            "none",
            "access_block",
            "render_failure",
            "wrong_target",
            "incomplete_content",
            "content_noise",
            "locale_mismatch",
            "unknown",
        }
        assert isinstance(case["human_issue_codes"], list)
        assert case["evaluation_goal"]
        assert case["label_notes"]
        assert case["source_url"].startswith(("http://", "https://"))
        datetime.fromisoformat(case["captured_at"])

        artifact = (CORPUS_ROOT / case["artifact"]).resolve()
        assert artifact.is_relative_to(CORPUS_ROOT.resolve())
        content = gzip.decompress(artifact.read_bytes()).decode("utf-8")
        assert len(content) == case["content_chars"]
        assert hashlib.sha256(content.encode("utf-8")).hexdigest() == case["content_sha256"]


def test_calibration_corpus_labels_match_the_binary_and_structured_contract() -> None:
    for case in _load_cases():
        if case["human_verdict"] == "pass":
            assert case["human_root_cause"] == "none"
            assert case["human_issue_codes"] == []
        else:
            assert case["human_root_cause"] != "none"
            assert case["human_issue_codes"]
