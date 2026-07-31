# LLM Research Module

Deep research capabilities for unknown factors and strategies.

## What It Does

1. **Unknown Factor Discovery**: identifies what the system doesn't know
2. **Deep Research**: uses LLM to research market microstructure, strategies, edge sources
3. **Strategy Brainstorming**: generates new strategy ideas
4. **Assumption Challenging**: questions every assumption in the system
5. **Cross-Session Learning**: shares knowledge across sessions

## Research Topics

### Current Research Queue
1. Roll Measure predictability in crypto futures (2026 data)
2. VPIN toxicity levels across Binance pairs
3. Bot herding patterns and feedback loops
4. Adverse selection in 1m scalping
5. Cross-asset correlation dynamics (BTC → altcoins)
6. Funding rate regimes and altcoin direction
7. Order flow toxicity detection methods
8. Market microstructure edge sources

### Research Output Format

```json
{
  "research_id": "research_001",
  "topic": "Roll Measure predictability",
  "source": "Easley et al 2024, SSRN 4814346",
  "key_findings": [
    "Roll Measure AUC 0.54-0.61 for price dynamics prediction",
    "High Roll = momentum is real, low Roll = mean-reverting",
    "Crypto markets have higher VPIN than traditional markets",
    "Cross-asset effects: BTC Roll + VPIN predict altcoin dynamics",
    "Effects stable across bull/bear regimes"
  ],
  "practical_implication": "Use Roll Measure + VPIN as entry filters",
  "confidence": 0.82,
  "actionable": true,
  "action": "Add Roll Measure filter and VPIN filter to entry logic",
  "timestamp": "2026-08-01T01:00:00Z"
}
```

## Agentic Brainstorming Loop

```
1. Identify unknown factor
2. Research factor using LLM
3. Evaluate factor for practical use
4. If actionable → add to factor engine
5. If not actionable → document and revisit
6. Meta-evaluator checks if research was useful
7. Adjust research priority based on meta-evaluation
```

## Integration with Evaluation System

The LLM research module is called when:
- A factor has "unknown" trust level
- The meta-evaluator detects unexpected behavior
- New market conditions emerge that existing factors don't cover
- The system needs to discover new edge sources

## Research Sources

- Academic papers (arXiv, SSRN)
- Market microstructure literature
- Crypto trading strategy research
- Bot consensus analysis
- Order flow analysis papers
- Cross-asset correlation studies