from __future__ import annotations

import gzip
import json
from types import SimpleNamespace
from pathlib import Path

import pytest


def _case(
    case_id: str,
    verdict: str,
    *,
    classification: str = "clean_article",
    root_cause: str = "none",
    issue_codes: list[str] | None = None,
    split: str = "dev",
) -> dict:
    return {
        "id": case_id,
        "classification": classification,
        "human_verdict": verdict,
        "human_root_cause": root_cause,
        "human_issue_codes": issue_codes or [],
        "split": split,
        "score": True,
    }


def _response(
    case_id: str,
    verdict: str,
    *,
    root_cause: str = "none",
    issue_codes: list[str] | None = None,
    review: bool = False,
    cost: float = 0.001,
    elapsed_ms: int = 100,
    model: str = "google/gemini-test",
    prompt_version: str = "scrape-usability-v2",
) -> dict:
    return {
        "case_id": case_id,
        "model": model,
        "prompt_version": prompt_version,
        "status": "completed",
        "elapsed_ms": elapsed_ms,
        "usage": {"cost": cost},
        "judgment": {
            "verdict": verdict,
            "root_cause": root_cause,
            "issues": [
                {
                    "code": code,
                    "severity": "high",
                    "source": "markdown",
                    "evidence": "fixture evidence",
                }
                for code in (issue_codes or [])
            ],
            "needs_human_review": review,
        },
    }


def test_metrics_report_binary_structured_review_cost_and_latency() -> None:
    from scrape_gateway.calibration import compute_metrics

    cases = [
        _case("tp", "pass"),
        _case("fn", "pass"),
        _case(
            "tn",
            "fail",
            classification="captcha_wall",
            root_cause="access_block",
            issue_codes=["captcha"],
        ),
        _case(
            "fp",
            "fail",
            classification="login_wall",
            root_cause="access_block",
            issue_codes=["login_wall"],
        ),
    ]
    responses = {
        "tp": _response("tp", "pass", elapsed_ms=100, cost=0.001),
        "fn": _response(
            "fn",
            "fail",
            root_cause="unknown",
            issue_codes=["other"],
            review=True,
            elapsed_ms=200,
            cost=0.002,
        ),
        "tn": _response(
            "tn",
            "fail",
            root_cause="access_block",
            issue_codes=["captcha"],
            elapsed_ms=300,
            cost=0.003,
        ),
        "fp": _response("fp", "pass", elapsed_ms=400, cost=0.004),
    }

    metrics = compute_metrics(cases, responses)

    assert metrics["verdict"]["confusion_matrix"] == {
        "true_fail_predicted_fail": 1,
        "true_fail_predicted_pass": 1,
        "true_pass_predicted_fail": 1,
        "true_pass_predicted_pass": 1,
    }
    assert metrics["verdict"]["tpr"] == 0.5
    assert metrics["verdict"]["tnr"] == 0.5
    assert metrics["verdict"]["precision"] == 0.5
    assert metrics["verdict"]["recall"] == 0.5
    assert metrics["verdict"]["f1"] == 0.5
    assert metrics["root_cause"]["accuracy"] == 0.5
    assert metrics["issue_codes"]["exact_match_accuracy"] == 0.5
    assert metrics["human_review"] == {
        "count": 1,
        "rate": 0.25,
        "model_errors": 2,
        "errors_flagged": 1,
        "error_recall": 0.5,
        "review_precision": 1.0,
    }
    assert metrics["cost"]["total"] == pytest.approx(0.01)
    assert metrics["cost"]["per_judgment"] == pytest.approx(0.0025)
    assert metrics["latency_ms"]["p50"] == 250
    assert metrics["latency_ms"]["p95"] == 385
    assert {item["id"] for item in metrics["disagreements"]} == {"fn", "fp"}


def test_metrics_use_byok_upstream_inference_cost() -> None:
    from scrape_gateway.calibration import compute_metrics

    response = _response("byok", "pass", cost=0)
    response["usage"].update(
        {
            "is_byok": True,
            "cost_details": {"upstream_inference_cost": 0.0041216},
        }
    )

    metrics = compute_metrics([_case("byok", "pass")], {"byok": response})

    assert metrics["cost"]["total"] == pytest.approx(0.0041216)
    assert metrics["cost"]["per_judgment"] == pytest.approx(0.0041216)


def test_deterministic_comparison_identifies_winner_and_category_recommendation() -> None:
    from scrape_gateway.calibration import compare_deterministic

    cases = [
        _case("free", "pass", classification="clean_article"),
        _case(
            "model",
            "fail",
            classification="paywall",
            root_cause="access_block",
            issue_codes=["paywall"],
        ),
        _case(
            "both",
            "fail",
            classification="captcha_wall",
            root_cause="access_block",
            issue_codes=["captcha"],
        ),
    ]
    responses = {
        "free": _response("free", "pass"),
        "model": _response(
            "model",
            "fail",
            root_cause="access_block",
            issue_codes=["paywall"],
        ),
        "both": _response(
            "both",
            "fail",
            root_cause="access_block",
            issue_codes=["captcha"],
        ),
    }
    deterministic = {"free": "pass", "model": "pass", "both": "fail"}

    comparison = compare_deterministic(cases, responses, deterministic)

    assert comparison["summary"] == {
        "agreements": 2,
        "disagreements": 1,
        "ai_wins": 1,
        "deterministic_wins": 0,
        "both_correct": 2,
        "both_wrong": 0,
    }
    assert comparison["disagreements"] == [
        {
            "id": "model",
            "classification": "paywall",
            "human_verdict": "fail",
            "deterministic_verdict": "pass",
            "ai_verdict": "fail",
            "winner": "ai",
        }
    ]
    categories = {item["classification"]: item for item in comparison["by_classification"]}
    assert categories["clean_article"]["recommendation"] == "free_checks"
    assert categories["paywall"]["recommendation"] == "model_call"
    assert categories["captcha_wall"]["recommendation"] == "free_checks"


def test_offline_report_replays_recorded_responses(tmp_path: Path) -> None:
    from scrape_gateway.calibration import build_report

    corpus_root = tmp_path / "v1"
    capture_root = corpus_root / "captures"
    response_root = tmp_path / "responses"
    capture_root.mkdir(parents=True)
    response_root.mkdir()
    html = "<html><main>Enough meaningful article content for deterministic checks.</main></html>"
    (capture_root / "one.html.gz").write_bytes(gzip.compress(html.encode("utf-8"), mtime=0))
    cases = [
        {
            **_case("one", "pass"),
            "artifact": "captures/one.html.gz",
            "status_code": 200,
        }
    ]
    (corpus_root / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    (response_root / "one.json").write_text(
        json.dumps(_response("one", "pass")),
        encoding="utf-8",
    )

    report = build_report(
        corpus_root=corpus_root,
        response_dir=response_root,
        split="dev",
        model="google/gemini-test",
    )

    assert report["mode"] == "offline"
    assert report["corpus_version"] == "v1"
    assert report["split"] == "dev"
    assert report["model"] == "google/gemini-test"
    assert report["case_count"] == 1
    assert report["metrics"]["verdict"]["tpr"] == 1.0
    assert report["deterministic_comparison"]["summary"]["both_correct"] == 1


def test_offline_report_rejects_missing_or_mismatched_responses(tmp_path: Path) -> None:
    from scrape_gateway.calibration import CalibrationError, build_report

    corpus_root = tmp_path / "v1"
    response_root = tmp_path / "responses"
    corpus_root.mkdir()
    response_root.mkdir()
    (corpus_root / "cases.json").write_text(
        json.dumps([_case("one", "pass")]),
        encoding="utf-8",
    )

    with pytest.raises(CalibrationError, match="Missing recorded response"):
        build_report(
            corpus_root=corpus_root,
            response_dir=response_root,
            split="dev",
            model="google/gemini-test",
        )

    (response_root / "one.json").write_text(
        json.dumps(_response("one", "pass", model="google/other-model")),
        encoding="utf-8",
    )
    with pytest.raises(CalibrationError, match="model mismatch"):
        build_report(
            corpus_root=corpus_root,
            response_dir=response_root,
            split="dev",
            model="google/gemini-test",
        )


def test_held_out_run_claim_is_one_shot(tmp_path: Path) -> None:
    from scrape_gateway.calibration import CalibrationError, claim_live_run

    cases = [
        _case("test-pass", "pass", split="test"),
        _case(
            "test-fail",
            "fail",
            split="test",
            root_cause="access_block",
            issue_codes=["captcha"],
        ),
    ]

    marker = claim_live_run(
        response_dir=tmp_path,
        cases=cases,
        split="test",
        model="google/gemini-test",
        prompt_version="scrape-usability-v2",
    )

    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["status"] == "started"
    assert payload["case_ids"] == ["test-pass", "test-fail"]
    with pytest.raises(CalibrationError, match="already been claimed"):
        claim_live_run(
            response_dir=tmp_path,
            cases=cases,
            split="test",
            model="google/gemini-test",
            prompt_version="scrape-usability-v2",
        )


def test_dev_run_does_not_create_a_held_out_marker(tmp_path: Path) -> None:
    from scrape_gateway.calibration import claim_live_run

    marker = claim_live_run(
        response_dir=tmp_path,
        cases=[_case("dev", "pass", split="dev")],
        split="dev",
        model="google/gemini-test",
        prompt_version="scrape-usability-v2",
    )

    assert marker is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_live_run_records_replayable_response_without_overwriting(
    tmp_path: Path,
) -> None:
    from scrape_gateway.calibration import CalibrationError, run_live
    from scrape_gateway.evaluation import EvaluationOutcome

    corpus_root = tmp_path / "v1"
    capture_root = corpus_root / "captures"
    response_root = tmp_path / "responses"
    capture_root.mkdir(parents=True)
    html = (
        "<html><main>"
        + "Meaningful article content for the requested extraction goal. " * 4
        + "</main></html>"
    )
    (capture_root / "one.html.gz").write_bytes(gzip.compress(html.encode("utf-8"), mtime=0))
    case = {
        **_case("one", "pass"),
        "source_url": "https://example.com/article",
        "evaluation_goal": "Capture the complete article.",
        "country": None,
        "capture_provider": "raw_http",
        "artifact": "captures/one.html.gz",
        "status_code": 200,
    }
    (corpus_root / "cases.json").write_text(
        json.dumps([case]),
        encoding="utf-8",
    )

    class FakeEvaluator:
        config = SimpleNamespace(model="google/gemini-test")

        def __init__(self) -> None:
            self.calls = []

        async def evaluate(self, **kwargs):
            self.calls.append(kwargs)
            return EvaluationOutcome(
                status="completed",
                model=self.config.model,
                judgment={
                    "verdict": "pass",
                    "root_cause": "none",
                    "issues": [],
                    "needs_human_review": False,
                },
                provider="test-provider",
                usage={"cost": 0.0002, "total_tokens": 42},
                elapsed_ms=123,
            )

    evaluator = FakeEvaluator()
    report = await run_live(
        corpus_root=corpus_root,
        response_dir=response_root,
        split="dev",
        model="google/gemini-test",
        evaluator=evaluator,
    )

    assert report["mode"] == "live"
    assert report["metrics"]["verdict"]["tpr"] == 1.0
    assert len(evaluator.calls) == 1
    call = evaluator.calls[0]
    assert call["request"].metadata["evaluation_goal"] == "Capture the complete article."
    assert call["result"].content_validated is True
    assert "Meaningful article content" in call["result"].markdown
    recorded = json.loads((response_root / "one.json").read_text(encoding="utf-8"))
    assert recorded["case_id"] == "one"
    assert recorded["usage"]["cost"] == 0.0002
    assert recorded["deterministic"]["verdict"] == "pass"

    with pytest.raises(CalibrationError, match="already exists"):
        await run_live(
            corpus_root=corpus_root,
            response_dir=response_root,
            split="dev",
            model="google/gemini-test",
            evaluator=evaluator,
        )


def test_response_directory_is_stable_and_rejects_unsafe_run_names(
    tmp_path: Path,
) -> None:
    from scrape_gateway.calibration import CalibrationError, response_directory

    assert (
        response_directory(
            tmp_path,
            run_name="baseline-v2",
            model="google/gemini-3.1-flash-lite",
        )
        == tmp_path / "baseline-v2" / "google__gemini-3.1-flash-lite"
    )
    with pytest.raises(CalibrationError, match="run name"):
        response_directory(
            tmp_path,
            run_name="../escape",
            model="google/gemini-3.1-flash-lite",
        )
