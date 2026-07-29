from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import random
import re
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from markdownify import markdownify as md

from .config import EvaluationConfig
from .errors import classify_failure
from .evaluation import PROMPT_VERSION, SYSTEM_PROMPT, OpenRouterEvaluator
from .models import ScrapeRequest, ScrapeResult
from .validators import validate_content


class CalibrationError(ValueError):
    pass


RUN_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def response_directory(
    root: str | Path,
    *,
    run_name: str,
    model: str,
) -> Path:
    if not RUN_NAME_PATTERN.fullmatch(run_name):
        raise CalibrationError(
            "Calibration run name must contain only letters, digits, dots, dashes, and underscores"
        )
    model_slug = re.sub(r"[^A-Za-z0-9._-]+", "__", model)
    return Path(root) / run_name / model_slug


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    if not denominator:
        return None
    return float(numerator / denominator)


def _percentile(values: list[int | float], quantile: float) -> int | float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = position - lower_index
    value = ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction
    return int(value) if float(value).is_integer() else value


def _bootstrap_rate_intervals(
    human: list[str],
    predicted: list[str],
    *,
    samples: int = 2_000,
    seed: int = 42,
) -> dict[str, list[float] | None]:
    if not human:
        return {"tpr": None, "tnr": None}
    rng = random.Random(seed)
    tprs: list[float] = []
    tnrs: list[float] = []
    for _ in range(samples):
        indices = [rng.randrange(len(human)) for _ in human]
        selected_human = [human[index] for index in indices]
        selected_predicted = [predicted[index] for index in indices]
        pass_count = selected_human.count("pass")
        fail_count = selected_human.count("fail")
        if pass_count:
            tprs.append(
                sum(
                    truth == "pass" and guess == "pass"
                    for truth, guess in zip(selected_human, selected_predicted, strict=True)
                )
                / pass_count
            )
        if fail_count:
            tnrs.append(
                sum(
                    truth == "fail" and guess == "fail"
                    for truth, guess in zip(selected_human, selected_predicted, strict=True)
                )
                / fail_count
            )
    return {
        "tpr": (
            [
                float(_percentile(tprs, 0.025)),
                float(_percentile(tprs, 0.975)),
            ]
            if tprs
            else None
        ),
        "tnr": (
            [
                float(_percentile(tnrs, 0.025)),
                float(_percentile(tnrs, 0.975)),
            ]
            if tnrs
            else None
        ),
    }


def _prediction(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("status") != "completed":
        raise CalibrationError(
            f"Recorded response for {record.get('case_id') or '?'} is not completed"
        )
    judgment = record.get("judgment")
    if not isinstance(judgment, dict):
        raise CalibrationError(
            f"Recorded response for {record.get('case_id') or '?'} has no judgment"
        )
    if judgment.get("verdict") not in {"pass", "fail"}:
        raise CalibrationError(
            f"Recorded response for {record.get('case_id') or '?'} has an invalid verdict"
        )
    return judgment


def _record_cost(record: dict[str, Any]) -> float:
    usage = record.get("usage") or {}
    cost = usage.get("cost")
    if isinstance(cost, (int, float)) and cost:
        return float(cost)
    upstream_cost = (usage.get("cost_details") or {}).get("upstream_inference_cost")
    if isinstance(upstream_cost, (int, float)):
        return float(upstream_cost)
    if isinstance(cost, (int, float)):
        return float(cost)
    generation = (record.get("response_metadata") or {}).get("generation") or {}
    for key in ("total_cost", "cost"):
        value = generation.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def compute_metrics(
    cases: list[dict[str, Any]],
    responses: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    tp = tn = fp = fn = 0
    root_matches = issue_matches = review_count = errors_flagged = reviewed_errors = 0
    disagreements = []
    costs: list[float] = []
    latencies: list[int | float] = []
    human_labels: list[str] = []
    predicted_labels: list[str] = []

    for case in cases:
        record = responses[case["id"]]
        judgment = _prediction(record)
        human = case["human_verdict"]
        predicted = judgment["verdict"]
        human_labels.append(human)
        predicted_labels.append(predicted)
        if human == "pass" and predicted == "pass":
            tp += 1
        elif human == "pass":
            fn += 1
        elif predicted == "fail":
            tn += 1
        else:
            fp += 1

        root_matches += judgment.get("root_cause") == case["human_root_cause"]
        predicted_issue_codes = {
            issue.get("code") for issue in judgment.get("issues") or [] if isinstance(issue, dict)
        }
        issue_matches += predicted_issue_codes == set(case["human_issue_codes"])
        needs_review = judgment.get("needs_human_review") is True
        review_count += needs_review
        is_error = human != predicted
        if is_error:
            errors_flagged += needs_review
            disagreements.append(
                {
                    "id": case["id"],
                    "classification": case["classification"],
                    "human_verdict": human,
                    "ai_verdict": predicted,
                    "needs_human_review": needs_review,
                    "human_root_cause": case["human_root_cause"],
                    "ai_root_cause": judgment.get("root_cause"),
                    "human_issue_codes": sorted(case["human_issue_codes"]),
                    "ai_issue_codes": sorted(predicted_issue_codes),
                }
            )
        if needs_review and is_error:
            reviewed_errors += 1
        costs.append(_record_cost(record))
        elapsed = record.get("elapsed_ms")
        if isinstance(elapsed, (int, float)):
            latencies.append(elapsed)

    count = len(cases)
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    model_errors = fp + fn
    return {
        "verdict": {
            "confusion_matrix": {
                "true_fail_predicted_fail": tn,
                "true_fail_predicted_pass": fp,
                "true_pass_predicted_fail": fn,
                "true_pass_predicted_pass": tp,
            },
            "accuracy": _ratio(tp + tn, count),
            "tpr": recall,
            "tnr": _ratio(tn, tn + fp),
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "bootstrap_95_ci": _bootstrap_rate_intervals(human_labels, predicted_labels),
        },
        "root_cause": {
            "correct": root_matches,
            "accuracy": _ratio(root_matches, count),
        },
        "issue_codes": {
            "exact_matches": issue_matches,
            "exact_match_accuracy": _ratio(issue_matches, count),
        },
        "human_review": {
            "count": review_count,
            "rate": _ratio(review_count, count),
            "model_errors": model_errors,
            "errors_flagged": errors_flagged,
            "error_recall": _ratio(errors_flagged, model_errors),
            "review_precision": _ratio(reviewed_errors, review_count),
        },
        "cost": {
            "total": sum(costs),
            "per_judgment": _ratio(sum(costs), count),
        },
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
        },
        "disagreements": disagreements,
    }


def compare_deterministic(
    cases: list[dict[str, Any]],
    responses: dict[str, dict[str, Any]],
    deterministic_verdicts: dict[str, str],
) -> dict[str, Any]:
    summary = {
        "agreements": 0,
        "disagreements": 0,
        "ai_wins": 0,
        "deterministic_wins": 0,
        "both_correct": 0,
        "both_wrong": 0,
    }
    disagreements = []
    by_classification: dict[str, dict[str, int]] = defaultdict(
        lambda: {"count": 0, "ai_correct": 0, "deterministic_correct": 0}
    )
    for case in cases:
        human = case["human_verdict"]
        ai = _prediction(responses[case["id"]])["verdict"]
        deterministic = deterministic_verdicts[case["id"]]
        ai_correct = ai == human
        deterministic_correct = deterministic == human
        category = by_classification[case["classification"]]
        category["count"] += 1
        category["ai_correct"] += ai_correct
        category["deterministic_correct"] += deterministic_correct
        if ai == deterministic:
            summary["agreements"] += 1
            summary["both_correct" if ai_correct else "both_wrong"] += 1
            continue
        summary["disagreements"] += 1
        winner = "ai" if ai_correct else "deterministic"
        summary[f"{winner}_wins"] += 1
        disagreements.append(
            {
                "id": case["id"],
                "classification": case["classification"],
                "human_verdict": human,
                "deterministic_verdict": deterministic,
                "ai_verdict": ai,
                "winner": winner,
            }
        )

    category_rows = []
    for classification, counts in sorted(by_classification.items()):
        ai_accuracy = counts["ai_correct"] / counts["count"]
        deterministic_accuracy = counts["deterministic_correct"] / counts["count"]
        if deterministic_accuracy == 1 or deterministic_accuracy > ai_accuracy:
            recommendation = "free_checks"
        elif ai_accuracy > deterministic_accuracy:
            recommendation = "model_call"
        else:
            recommendation = "human_review"
        category_rows.append(
            {
                "classification": classification,
                **counts,
                "ai_accuracy": ai_accuracy,
                "deterministic_accuracy": deterministic_accuracy,
                "recommendation": recommendation,
            }
        )
    return {
        "summary": summary,
        "disagreements": disagreements,
        "by_classification": category_rows,
    }


def _load_cases(corpus_root: Path, split: str) -> list[dict[str, Any]]:
    try:
        cases = json.loads((corpus_root / "cases.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"Could not load corpus: {exc}") from exc
    selected = [
        case
        for case in cases
        if case.get("score") is True and (split == "all" or case.get("split") == split)
    ]
    if not selected:
        raise CalibrationError(f"No scored cases found for split {split!r}")
    return selected


def _load_responses(
    cases: list[dict[str, Any]],
    response_dir: Path,
    *,
    model: str,
    prompt_version: str,
) -> dict[str, dict[str, Any]]:
    responses = {}
    for case in cases:
        path = response_dir / f"{case['id']}.json"
        if not path.exists():
            raise CalibrationError(f"Missing recorded response: {path}")
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CalibrationError(f"Could not load recorded response {path}: {exc}") from exc
        if record.get("case_id") != case["id"]:
            raise CalibrationError(f"{path}: case ID mismatch")
        if record.get("model") != model:
            raise CalibrationError(f"{path}: model mismatch")
        if record.get("prompt_version") != prompt_version:
            raise CalibrationError(f"{path}: prompt version mismatch")
        _prediction(record)
        responses[case["id"]] = record
    return responses


def _deterministic_verdicts(
    corpus_root: Path,
    cases: list[dict[str, Any]],
) -> dict[str, str]:
    verdicts = {}
    for case in cases:
        artifact = corpus_root / case["artifact"]
        content = gzip.decompress(artifact.read_bytes()).decode("utf-8")
        validation = validate_content(content)
        failure = classify_failure(case["status_code"], content)
        verdicts[case["id"]] = "pass" if validation.passed and failure is None else "fail"
    return verdicts


def build_report(
    *,
    corpus_root: str | Path,
    response_dir: str | Path,
    split: str,
    model: str,
    prompt_version: str = PROMPT_VERSION,
) -> dict[str, Any]:
    corpus_path = Path(corpus_root)
    response_path = Path(response_dir)
    cases = _load_cases(corpus_path, split)
    responses = _load_responses(
        cases,
        response_path,
        model=model,
        prompt_version=prompt_version,
    )
    deterministic = _deterministic_verdicts(corpus_path, cases)
    return {
        "mode": "offline",
        "corpus_version": corpus_path.name,
        "split": split,
        "model": model,
        "prompt_version": prompt_version,
        "case_count": len(cases),
        "metrics": compute_metrics(cases, responses),
        "deterministic_comparison": compare_deterministic(cases, responses, deterministic),
    }


def claim_live_run(
    *,
    response_dir: str | Path,
    cases: list[dict[str, Any]],
    split: str,
    model: str,
    prompt_version: str,
) -> Path | None:
    if split != "test":
        return None
    target = Path(response_dir)
    target.mkdir(parents=True, exist_ok=True)
    marker = target / ".held-out-run.json"
    payload = {
        "status": "started",
        "model": model,
        "prompt_version": prompt_version,
        "case_ids": [case["id"] for case in cases],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with marker.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except FileExistsError as exc:
        raise CalibrationError(f"Held-out run has already been claimed in {response_dir}") from exc
    return marker


def _case_evidence(
    corpus_root: Path,
    case: dict[str, Any],
) -> tuple[ScrapeRequest, ScrapeResult, list[dict[str, Any]], dict[str, Any]]:
    artifact = corpus_root / case["artifact"]
    content = gzip.decompress(artifact.read_bytes()).decode("utf-8")
    validation = validate_content(content)
    failure = classify_failure(case["status_code"], content)
    success = 200 <= int(case["status_code"]) < 400 and failure is None and validation.passed
    request = ScrapeRequest(
        case["source_url"],
        country=case.get("country"),
        metadata={"evaluation_goal": case["evaluation_goal"]},
    )
    result = ScrapeResult(
        url=case["source_url"],
        provider=case["capture_provider"],
        success=success,
        status_code=case["status_code"],
        html=content,
        markdown=md(content),
        failure_reason=failure,
        cost_units=0,
        route=case["capture_provider"],
        content_validated=validation.passed,
        block_type=validation.block_type,
        validation_detail=validation.detail,
    )
    attempt = {
        "provider": case["capture_provider"],
        "status": case["status_code"],
        "elapsed_ms": 0,
        "route": case["capture_provider"],
        "cost": 0,
        "result": "success" if success else "failed",
        "chars": len(content),
    }
    if failure:
        attempt["failure_reason"] = failure.value
    if validation.block_type:
        attempt["block_type"] = validation.block_type
    deterministic = {
        "verdict": "pass" if success else "fail",
        "validation_passed": validation.passed,
        "block_type": validation.block_type,
        "validation_detail": validation.detail,
        "classified_failure": failure.value if failure else None,
    }
    return request, result, [attempt], deterministic


async def run_live(
    *,
    corpus_root: str | Path,
    response_dir: str | Path,
    split: str,
    model: str,
    evaluator: Any | None = None,
    concurrency: int = 4,
) -> dict[str, Any]:
    if concurrency < 1:
        raise CalibrationError("Live concurrency must be at least 1")
    corpus_path = Path(corpus_root)
    response_path = Path(response_dir)
    cases = _load_cases(corpus_path, split)
    response_path.mkdir(parents=True, exist_ok=True)
    existing = [
        response_path / f"{case['id']}.json"
        for case in cases
        if (response_path / f"{case['id']}.json").exists()
    ]
    if existing:
        raise CalibrationError(f"Recorded response already exists: {existing[0]}")

    cache_dir: tempfile.TemporaryDirectory[str] | None = None
    if evaluator is None:
        cache_dir = tempfile.TemporaryDirectory(prefix="scrape-gateway-evaluator-calibration-")
        evaluator = OpenRouterEvaluator(
            EvaluationConfig(
                mode="audit",
                model=model,
                include_screenshot=False,
                cache_root=cache_dir.name,
            )
        )
        if not evaluator.api_key:
            cache_dir.cleanup()
            raise CalibrationError("OPENROUTER_API_KEY is not configured")
    evaluator_model = getattr(getattr(evaluator, "config", None), "model", None)
    if evaluator_model != model:
        if cache_dir:
            cache_dir.cleanup()
        raise CalibrationError(f"Evaluator model mismatch: expected {model}, got {evaluator_model}")

    marker = claim_live_run(
        response_dir=response_path,
        cases=cases,
        split=split,
        model=model,
        prompt_version=PROMPT_VERSION,
    )
    semaphore = asyncio.Semaphore(concurrency)
    prompt_sha256 = hashlib.sha256(f"{PROMPT_VERSION}\n{SYSTEM_PROMPT}".encode("utf-8")).hexdigest()
    failures: list[str] = []

    async def evaluate_case(case: dict[str, Any]) -> None:
        request, result, attempts, deterministic = _case_evidence(corpus_path, case)
        async with semaphore:
            outcome = await evaluator.evaluate(
                request=request,
                result=result,
                attempts=attempts,
                elapsed_ms=0,
            )
        record = {
            "schema_version": 1,
            "corpus_version": corpus_path.name,
            "case_id": case["id"],
            "classification": case["classification"],
            "split": case["split"],
            "model": model,
            "prompt_version": outcome.prompt_version,
            "prompt_sha256": prompt_sha256,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "status": outcome.status,
            "judgment": outcome.judgment,
            "generation_id": outcome.generation_id,
            "provider": outcome.provider,
            "usage": outcome.usage,
            "elapsed_ms": outcome.elapsed_ms,
            "input_modalities": outcome.input_modalities,
            "response_metadata": outcome.response_metadata,
            "error": outcome.error,
            "deterministic": deterministic,
        }
        (response_path / f"{case['id']}.json").write_text(
            json.dumps(
                record,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
        if outcome.status != "completed":
            failures.append(f"{case['id']}: {outcome.status}: {outcome.error}")

    try:
        await asyncio.gather(*(evaluate_case(case) for case in cases))
    finally:
        if cache_dir:
            cache_dir.cleanup()
    if failures:
        raise CalibrationError("Live evaluator did not complete every case: " + "; ".join(failures))
    if marker:
        marker_payload = json.loads(marker.read_text(encoding="utf-8"))
        marker_payload.update(
            {
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        marker.write_text(
            json.dumps(marker_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    report = build_report(
        corpus_root=corpus_path,
        response_dir=response_path,
        split=split,
        model=model,
    )
    report["mode"] = "live"
    return report
