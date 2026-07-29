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

The `final` run claimed the held-out split once on 2026-07-29. The claim marker and
all recorded responses are committed under
`tests/fixtures/evaluator_calibration/v1/responses/final/`.

| Metric | Held-out result |
|---|---:|
| Cases | 24 |
| Confusion matrix | 11 true fail, 1 false pass, 0 false fail, 12 true pass |
| TPR | 100% (95% bootstrap interval 100–100%) |
| TNR | 91.7% (95% bootstrap interval 72.7–100%) |
| Precision | 92.3% |
| Recall | 100% |
| F1 | 96.0% |
| Root-cause accuracy | 83.3% (20/24) |
| Exact issue-code accuracy | 87.5% (21/24) |
| Cost | $0.054444 total; $0.002269 per judgment |
| Latency | 2.913s p50; 4.003s p95 |

The sole verdict error was a false pass on
`captcha_2captcha_recaptcha_demo`. The deterministic checks correctly rejected
the page as a CAPTCHA wall. No tuning, model change, or second holdout call was
made after this result.

Across all 60 cases, the selected model was correct on 59/60 verdicts (98.3%),
with 100% TPR, 96.7% TNR, and 98.4% F1. Root-cause accuracy was 90.0% and exact
issue-code accuracy was 81.7%. The selected model's 60 recorded judgments cost
$0.148347 in upstream inference, or $0.002472 each, with 3.021s p50 and 4.042s
p95 latency.

## Free checks versus the model

The deterministic combination of `validate_content` and `classify_failure` was
correct on 43/60 cases. The model was correct on 59/60. They agreed on 42 correct
decisions; among 18 disagreements, the model was right 17 times and the free
checks once.

| Page class | Cases | Free checks | AI | Recommendation |
|---|---:|---:|---:|---|
| Bot block | 4 | 4/4 | 4/4 | Free checks |
| CAPTCHA wall | 4 | 4/4 | 3/4 | Free checks |
| Clean article | 6 | 6/6 | 6/6 | Free checks |
| Clean listing | 6 | 6/6 | 6/6 | Free checks |
| Clean product | 6 | 6/6 | 6/6 | Free checks |
| Adversarial terms in legitimate content | 6 | 3/6 | 6/6 | Model call |
| Ordinary cookie mentions | 6 | 5/6 | 6/6 | Model call |
| Cookie wall | 3 | 3/3 | 3/3 | Free checks |
| Empty response | 3 | 3/3 | 3/3 | Free checks |
| JavaScript shell | 3 | 0/3 | 3/3 | Model call |
| Login wall | 4 | 0/4 | 4/4 | Model call |
| Paywall | 3 | 0/3 | 3/3 | Model call |
| Truncated content | 3 | 0/3 | 3/3 | Model call |
| Wrong locale | 3 | 3/3 | 3/3 | Free checks |

Do not pay for an AI judgment when free checks already identify an obvious bot
block, CAPTCHA, cookie wall, empty response, or ordinary clean page. Use the
model for the measured ambiguity classes: legitimate pages containing block
terminology, ordinary cookie discussions, JavaScript shells, login walls,
paywalls, and materially truncated content. The category samples are small, so
recalibrate after changing validators, prompt, or model rather than treating
these rates as timeless.

## Human-review escape hatch

The escape hatch is unreliable and must not be depended on. The 3.1 candidate
marked none of its two train/dev verdict errors for human review. The selected
3.5 model made no train/dev verdict errors, then set `needs_human_review: false`
on its only held-out error. Its measured review-on-error recall is therefore
0/1 on holdout and 0/1 across all 60 selected-model cases.

The exact machine-readable numbers and category disagreements live in
`tests/fixtures/evaluator_calibration/v1/results.json`. The committed response
files make the report replayable offline with no network or OpenRouter key.
