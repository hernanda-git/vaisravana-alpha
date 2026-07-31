# Meta-Orchestrator

Coordinates all meta-evaluators, dynamic factors, and LLM research.

## Orchestration Flow

```
Trade completes
    ↓
Layer 1: Per-Trade Evaluator → trade_score
    ↓
Layer 2: Aggregate Evaluator → aggregate_score
    ↓
Layer 3: Baseline Evaluator → baseline_score
    ↓
Layer 4: Decision Gate → decision
    ↓
Layer 5: Report Generator → report
    ↓
Meta-Evaluator checks all layers → trust_levels
    ↓
Dynamic Factor Engine updates weights → factor_weights
    ↓
LLM Research Module researches unknowns → new_factors
    ↓
Unknown Factor Discoverer finds gaps → research_queue
    ↓
Meta-Orchestrator combines everything → final_decision
    ↓
Persist / Iterate / Rollback / Research More
```

## Key Methods

```python
class MetaOrchestrator:
    def coordinate(self, trade_data: dict) -> dict:
        """Run full pipeline including meta-evaluation."""
        # Run all evaluators
        l1 = self.layer1.evaluate(trade_data)
        l2 = self.layer2.evaluate_window()
        l3 = self.layer3.compare_baseline()
        l4 = self.layer4.decide(l1, l2, l3)
        l5 = self.layer5.generate_report(l1, l2, l3, l4)

        # Meta-evaluate all evaluators
        meta_scores = self.meta_evaluator.evaluate_all(l1, l2, l3, l4, l5)

        # Update dynamic factors
        factors = self.factor_engine.update(meta_scores)

        # Research unknowns
        unknowns = self.unknown_discoverer.find_gaps(meta_scores)
        research_results = self.research_module.research(unknowns)

        # Combine everything
        final_decision = self.combine(
            l1, l2, l3, l4, l5,
            meta_scores, factors, research_results
        )

        return final_decision
```

## Self-Improvement Loop

```
evaluate → meta-evaluate → adjust → re-evaluate → ...
```

The system continuously improves by:
1. Checking if evaluators are trustworthy
2. Adjusting factor weights based on performance
3. Researching unknown factors
4. Discovering new edge sources
5. Challenging assumptions
6. Iterating until confidence is high