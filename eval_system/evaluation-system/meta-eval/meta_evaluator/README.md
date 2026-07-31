# Meta-Evaluator: Evaluates the Evaluators

The meta-evaluator checks if each layer evaluator is doing its job correctly.

## What It Evaluates

For each evaluator (Layer 1-5), the meta-evaluator checks:

1. **Consistency**: does the evaluator produce similar results for similar inputs?
2. **Sensitivity**: does the evaluator respond to changes in input?
3. **Stability**: does the evaluator produce stable outputs over time?
4. **Discrimination**: can the evaluator distinguish good trades from bad trades?
5. **Timeliness**: does the evaluator detect degradation early enough?

## Meta-Evaluation Metrics

```
meta_consistency_score = 1.0 - variance_of_similar_inputs
meta_sensitivity_score = correlation_of_output_with_input_change
meta_stability_score = 1.0 - coefficient_of_variation_over_time
meta_discrimination_score = AUC_of_trade_quality_prediction
meta_timeliness_score = lead_time_before_degradation_detected
```

## Meta-Evaluation Output

```json
{
  "meta_eval_id": "meta_001",
  "evaluator_layer": "layer2_aggregate",
  "consistency_score": 0.85,
  "sensitivity_score": 0.72,
  "stability_score": 0.91,
  "discrimination_score": 0.68,
  "timeliness_score": 0.75,
  "overall_meta_score": 0.78,
  "trust_level": "high",  // high / medium / low / unknown
  "recommendation": "use_as_primary",  // use_as_primary / use_with_caution / discard / research_more
  "timestamp": "2026-08-01T01:00:00Z"
}
```

## Trust Levels

- **high** (meta_score >= 0.75): use as primary evaluator
- **medium** (meta_score 0.50-0.74): use with caution, cross-check with other evaluators
- **low** (meta_score 0.25-0.49): do not trust, investigate why
- **unknown** (meta_score < 0.25): discard, need LLM research to understand why

## Meta-Evaluation Loop

```
evaluator produces output
    ↓
meta-evaluator checks consistency, sensitivity, stability, discrimination, timeliness
    ↓
if trust_level == "high": use output directly
if trust_level == "medium": use output but flag for review
if trust_level == "low": investigate, adjust evaluator
if trust_level == "unknown": send to LLM research module
    ↓
adjusted evaluator produces new output
    ↓
meta-evaluator re-checks
    ↓
repeat until trust_level == "high" or max iterations reached
```