# Unknown Factor Discoverer

Finds what the evaluation system doesn't know.

## How It Works

1. **Gap Analysis**: compares current factors against known market microstructure
2. **Performance Anomaly Detection**: finds patterns the evaluators miss
3. **Cross-Reference Check**: checks if known factors from research are implemented
4. **Novelty Search**: finds factors no bot is currently using

## Gap Categories

### Known but Not Implemented
- Factors that exist in research but not in the bot
- Example: Roll Measure, VPIN, herding detection

### Unknown but Discoverable
- Factors that can be discovered through research
- Example: cross-asset correlation dynamics, funding rate regimes

### Unknown and Unknowable (for now)
- Factors that require data we don't have access to
- Example: institutional order flow, dark pool activity

### Known but Misunderstood
- Factors that exist but are interpreted incorrectly
- Example: CVD divergence used as veto instead of entry amplifier

## Output

```json
{
  "discoverer_id": "discoverer_001",
  "known_not_implemented": [
    "roll_measure",
    "vpin",
    "herding_detection"
  ],
  "unknown_discoverable": [
    "cross_asset_correlation_dynamics",
    "funding_rate_regimes",
    "order_flow_toxicity_detection"
  ],
  "unknown_unknowable": [
    "institutional_order_flow",
    "dark_pool_activity"
  ],
  "known_misunderstood": [
    "cvd_divergence_veto_only"
  ],
  "research_priority": [
    "roll_measure",
    "vpin",
    "herding_detection"
  ],
  "timestamp": "2026-08-01T01:00:00Z"
}
```