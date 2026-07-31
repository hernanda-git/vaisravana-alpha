# Dynamic Factor Engine

Factors that adapt to market conditions in real-time.

## Factor Categories

### 1. Regime Factors (change with market state)
- trend_strength: ADX-based (0-1)
- vol_regime: ATR percentile (low/normal/high/extreme)
- liquidity_regime: spread-based (tight/normal/wide/extreme)
- direction_regime: bullish/bearish/neutral

### 2. Flow Factors (change with order flow)
- cvd_direction: buy/sell pressure (normalized -1 to 1)
- cvd_momentum: rate of change of CVD
- book_imbalance: bid/ask depth ratio (normalized)
- flow_toxicity: VPIN-based (0-1)

### 3. Cross-Asset Factors (change with BTC)
- btc_trend: BTC EMA slope (bullish/bearish/neutral)
- btc_vol: BTC volatility regime
- btc_correlation: current altcoin-BTC correlation
- btc_lead: BTC leading indicator (BTC moves first)

### 4. Temporal Factors (change with time)
- session_regime: Asian/European/American/Asian overlap
- hour_of_day: 0-23 (affects volatility and direction)
- day_of_week: Mon-Fri (affects liquidity patterns)
- consecutive_losses: recent losing streak length

### 5. LLM-Discovered Factors (unknown, researched)
- roll_measure: autocorrelation-based predictability
- vpin_level: volume-synchronized informed trading probability
- herding_intensity: how many bots are trading same strategy
- adverse_selection_risk: likelihood of trading into toxic flow
- feedback_loop_strength: how much bot consensus is amplifying moves

## Dynamic Weight Adjustment

Factor weights are NOT fixed. They are adjusted by the meta-evaluator based on:

1. How well each factor predicted recent outcomes
2. How stable each factor is across regimes
3. How much each factor contributes to aggregate score

Weight update formula:
```
new_weight = old_weight * (1 + learning_rate * prediction_accuracy)
```

Where prediction_accuracy is how well the factor predicted trade outcomes in the last window.

## Output

```json
{
  "factor_id": "dynamic_001",
  "regime": {
    "trend_strength": 0.72,
    "vol_regime": "high",
    "liquidity_regime": "normal",
    "direction_regime": "bullish"
  },
  "flow": {
    "cvd_direction": 0.45,
    "cvd_momentum": 0.12,
    "book_imbalance": 0.68,
    "flow_toxicity": 0.23
  },
  "cross_asset": {
    "btc_trend": "bullish",
    "btc_vol": "normal",
    "btc_correlation": 0.82,
    "btc_lead": true
  },
  "temporal": {
    "session_regime": "European",
    "hour_of_day": 14,
    "day_of_week": "Monday",
    "consecutive_losses": 2
  },
  "llm_discovered": {
    "roll_measure": 0.58,
    "vpin_level": 0.35,
    "herding_intensity": 0.42,
    "adverse_selection_risk": 0.28,
    "feedback_loop_strength": 0.55
  },
  "factor_weights": {
    "regime": 0.25,
    "flow": 0.20,
    "cross_asset": 0.15,
    "temporal": 0.15,
    "llm_discovered": 0.25
  },
  "timestamp": "2026-08-01T01:00:00Z"
}
```