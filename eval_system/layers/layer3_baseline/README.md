# Layer 3: Baseline Comparison

Compares current strategy performance against baselines.

## Baselines

1. **Random baseline**: simulated random trades at same frequency
2. **Buy-hold baseline**: buy and hold the same pair for same duration
3. **Previous period**: compare against last evaluation window
4. **Fee-only baseline**: what would happen if no trades were taken (only fees paid)

## Metrics

- alpha_vs_random: excess return over random baseline
- alpha_vs_buyhold: excess return over buy-hold
- information_ratio: alpha / tracking error vs previous period
- beat_random: true if alpha_vs_random > 0
- beat_buyhold: true if alpha_vs_buyhold > 0
- beat_previous: true if current WR > previous WR

## Output

```json
{
  "baseline_id": "wave_window_005",
  "alpha_vs_random": 0.032,
  "alpha_vs_buyhold": -0.015,
  "information_ratio": 0.85,
  "beat_random": true,
  "beat_buyhold": false,
  "beat_previous": true,
  "baseline_score": 0.55,
  "timestamp": "2026-08-01T01:00:00Z"
}
```