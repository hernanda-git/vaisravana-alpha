# Autonomous Multi-Layer Evaluation System

Version: 1.0.0
Date: 2026-08-01
Owner: valarion

## Architecture

5-layer autonomous evaluation pipeline:

  Layer 1: Per-Trade Evaluation (each trade)
  Layer 2: Aggregate Evaluation (every 20 trades)
  Layer 3: Baseline Comparison (vs random, buy-hold, previous period)
  Layer 4: Decision Gate (go/no-go on strategy changes)
  Layer 5: Report Generator (human-readable output + Telegram alert)

## Key Design Principles

1. Autonomous - runs in loop, no human intervention needed
2. Multi-layer - no single layer makes final decision
3. Additive - layers build on each other
4. Fast - evaluation completes in <100ms per window
5. Persistent - all results stored in DB
6. Self-correcting - auto-rollback if strategy degrades

## Integration

Wave bot: /opt/bots/vaisravana-wave/
Main bot: ~/vaisravana-workspace/vaisravana/
Alpha bot: /root/vaisravana-alpha/