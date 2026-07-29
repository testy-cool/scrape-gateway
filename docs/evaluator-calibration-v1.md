# Evaluator calibration v1

This document records the calibration of the advisory `scrape-usability-v2`
evaluator against the human-labelled `v1` corpus. The corpus has 60 balanced,
scored cases split into 10 train, 26 dev, and 24 held-out test cases. Captures
were made only with the zero-cost `raw_http` and `curl_cffi` providers.

## Pre-registered model selection

The selection rule below was locked on 2026-07-29 before claiming or evaluating
the held-out test split:

1. A candidate must reach at least 90% TPR and 90% TNR on the combined train and
   dev evidence.
2. Among eligible candidates, prefer the highest minimum of TPR and TNR, then
   verdict F1, root-cause accuracy, and exact issue-code accuracy.
3. Use cost per judgment, p50 latency, and p95 latency only as tie-breakers after
   judgment quality.
4. Run the held-out test split exactly once for the selected model. Do not tune
   the prompt, switch models, or rerun the held-out split after seeing results.

The combined train and dev comparison was:

| Model | TPR | TNR | F1 | Root cause | Issue codes | Cost / judgment | p50 | p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `google/gemini-3.1-flash-lite` | 88.9% | 100% | 94.1% | 72.2% | 72.2% | $0.002015 | 3.344s | 6.272s |
| `google/gemini-3.5-flash-lite` | 100% | 100% | 100% | 94.4% | 77.8% | $0.002608 | 3.064s | 3.956s |

`google/gemini-3.5-flash-lite` won before the holdout was opened. It was the
only candidate to clear both verdict-rate thresholds on the combined evidence,
made no verdict errors, and had substantially better root-cause accuracy. Its
measured judgment cost was higher, but quality takes precedence under the
pre-registered rule.

The 3.5 responses were served through OpenRouter with BYOK billing. In that
mode, OpenRouter reports `usage.cost` as zero and exposes the real model cost in
`usage.cost_details.upstream_inference_cost`; calibration cost summaries include
that upstream cost.

## Held-out result

Pending the single pre-registered run of the 24-case test split.

## Free checks versus the model

Pending the held-out run and the final per-category comparison over all 60 cases.

## Human-review escape hatch

On train and dev, 3.1 marked none of its two verdict errors for human review.
The selected 3.5 model made no verdict errors in those splits, so review-on-error
recall was not measurable before the holdout.
