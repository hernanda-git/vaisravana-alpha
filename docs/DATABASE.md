# Database and telemetry design

## Existing tables

- `wave_log`: one durable lifecycle row per wave.
- `wave_telemetry`: time-series observations while a wave is open.
- `smc_zones`: detected structure and zone state.
- `trades`: durable trade economics for compatibility and historical queries.

## Required future fields

Add through migrations, never destructive rewrites:

- `strategy_mode`: single_leg, paired_relative_value, basis.
- `trade_group_id`: groups coordinated legs.
- `leg_id`: identifies a leg within a group.
- `open_fee`, `close_fee`, `slippage`, `funding`.
- `gross_pnl`, `net_pnl`.
- `expected_gross`, `expected_cost`, `expected_net`.
- `regime`, `entry_rank`, `universe_snapshot_id`.
- `cvd_divergence_present`, `correlation`, `hedge_ratio`, `spread_z_entry`, `spread_z_exit`.

## Data lifecycle

1. Persist entry snapshot and cost estimate.
2. Persist each execution acknowledgement and fee.
3. Append open telemetry at a controlled interval.
4. Persist exit snapshot and all close costs.
5. Validate the accounting identity.
6. Expose the same rows to Telegram and offline reports.

## Retention

Trade lifecycle data is permanent for paper evaluation. High-frequency telemetry may be compacted only after derived features and decision snapshots are retained. Never delete historical trades to make a strategy appear profitable.

## Agentic evaluation

Agents should query by strategy mode, date interval, pair, side, regime, and exit reason. They should compare expected versus realized edge and flag accounting gaps before suggesting parameter changes.

## Integrity checks

- unique wave or trade-group identifiers;
- no orphaned legs;
- close timestamp after open timestamp;
- fees nonnegative;
- net equals gross minus all costs within rounding tolerance;
- no open row silently counted as closed;
- Telegram aggregate equals the database query.