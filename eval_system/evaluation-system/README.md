# Autonomous Meta-Evaluation System v2

Version: 2.0.0
Date: 2026-08-01
Owner: valarion

## Redesign Philosophy

The old system had static evaluators with fixed weights and hardcoded metrics.
The new system is:

1. **Meta-evaluators that evaluate evaluators** — no single evaluator is trusted
2. **Dynamic factors** — factors that adapt to market conditions
3. **LLM-driven research** — deep research for unknown factors
4. **Unknown factor discovery** — the system finds what it doesn't know
5. **Self-improving** — the system gets better over time
6. **Agentic brainstorming** — the system thinks differently, challenges assumptions

## Architecture

```
                    ┌─────────────────────────────────┐
                    │        META ORCHESTRATOR          │
                    │   (coordinates all meta-eval)     │
                    └──────────┬──────────────────────┘
                               │
         ┌─────────────────────┼─────────────────────┐
         │                     │                     │
    ┌────▼────┐          ┌────▼────┐          ┌────▼────┐
    │  Layer  │          │  Layer  │          │  Layer  │
    │  Eval   │    →     │  Meta   │    →     │  LLM    │
    │  Engine │          │  Eval   │          │  Research│
    └────┬────┘          └────┬────┘          └────┬────┘
         │                     │                     │
    ┌────▼─────────────────────▼─────────────────────▼────┐
    │              DYNAMIC FACTOR ENGINE                    │
    │  static factors + dynamic factors + llm discovered   │
    └──────────────────────────────────────────────────────┘
         │
    ┌────▼──────────────────────────────────────────────┐
    │         DECISION GATE (meta-level)                  │
    │  combine all evaluator outputs + factor weights    │
    │  → persist / iterate / rollback / research more    │
    └────────────────────────────────────────────────────┘
```

## Key Components

1. **Meta-Evaluator**: evaluates the output of other evaluators
2. **Dynamic Factor Engine**: adapts factor weights based on market regime
3. **LLM Research Module**: deep research on unknown factors
4. **Unknown Factor Discoverer**: finds what the system doesn't know
5. **Self-Improvement Loop**: meta-evaluator → factor adjustment → re-evaluate

## Factor Types

### Static Factors (known, fixed)
- win_rate, avg_r, net_pnl, fee_drag, sharpe_ratio

### Dynamic Factors (change with market)
- regime_type (trending/choppy/mean-reverting)
- vol_regime (high/low/normal)
- liquidity_regime (tight/wide/extreme)
- flow_regime (informed/noisy/toxic)

### LLM-Discovered Factors (unknown, researched)
- Roll Measure predictability
- VPIN toxicity level
- Herding intensity
- Adverse selection risk
- Cross-asset correlation strength
- Funding rate regime
- Order flow imbalance trend

### Adaptive Factors (self-adjusting)
- factor_weights (updated by meta-evaluator)
- entry_threshold (adjusted by performance)
- position_size (adjusted by edge strength)
- frequency_cap (adjusted by market conditions)