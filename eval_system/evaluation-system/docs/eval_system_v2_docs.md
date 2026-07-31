# Evaluation System v2 — Complete Documentation

## Overview

Autonomous meta-evaluation system that evaluates evaluators, discovers unknown factors, and self-improves over time.

## Architecture

```
Meta-Orchestrator
  ├── Meta-Evaluator (evaluates evaluators)
  ├── Dynamic Factor Engine (adaptive factors)
  ├── LLM Research Module (deep research)
  ├── Unknown Factor Discoverer (finds gaps)
  └── Decision Gate (final go/no-go)
```

## 5 Evaluation Layers

1. **Layer 1**: Per-Trade Evaluation — each trade scored individually
2. **Layer 2**: Aggregate Evaluation — window of 20 trades
3. **Layer 3**: Baseline Comparison — vs random, buy-hold, previous
4. **Layer 4**: Decision Gate — persist/iterate/rollback/pause/stop
5. **Layer 5**: Report Generator — human-readable + Telegram alert

## Meta-Evaluation

Each evaluator is itself evaluated:
- Consistency, Sensitivity, Stability, Discrimination, Timeliness
- Trust level: high/medium/low/unknown
- Auto-adjusts weights based on trust

## Dynamic Factors

Factors that adapt to market conditions:
- Regime factors (trend, vol, liquidity, direction)
- Flow factors (CVD, book imbalance, toxicity)
- Cross-asset factors (BTC trend, correlation)
- Temporal factors (session, hour, day)
- LLM-discovered factors (Roll Measure, VPIN, herding)

## LLM Research

Deep research for unknown factors:
- Roll Measure predictability
- VPIN toxicity levels
- Bot herding patterns
- Adverse selection in scalping
- Cross-asset correlation dynamics

## Unknown Factor Discovery

Finds what the system doesn't know:
- Known but not implemented
- Unknown but discoverable
- Unknown and unknowable
- Known but misunderstood

## Integration

Each bot gets an adapter:
- Wave adapter: `/opt/bots/vaisravana-wave/eval_system/`
- Main adapter: `~/vaisravana-workspace/vaisravana/eval_system/`
- Alpha adapter: `/root/vaisravana-alpha/eval_system/`