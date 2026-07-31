"""
Autonomous Multi-Layer Evaluation Engine v2
Core orchestration for 5-layer evaluation pipeline with meta-evaluation.

Fixed bugs from v1:
- run_layer5() now properly receives layer3 parameter
- All dataclass fields properly initialized
- JSON serialization handles enums and dataclasses correctly
"""

import json
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any
from pathlib import Path


class Verdict(Enum):
    WIN = "win"
    LOSS = "loss"
    SCRATCH = "scratch"


class Decision(Enum):
    PERSIST = "persist_changes"
    ITERATE = "iterate"
    ROLLBACK = "rollback"
    ROLLBACK_IMMEDIATE = "rollback_immediate"
    PAUSE = "pause_trading"
    STOP = "stop_trading"


class Trend(Enum):
    IMPROVING = "improving"
    STABLE = "stable"
    DECLINING = "declining"


class TrustLevel(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


@dataclass
class TradeResult:
    trade_id: str
    bot: str
    pair: str
    side: str
    entry: float
    sl: float
    tp: float
    exit_price: float
    r_achieved: float
    fee_open: float
    fee_close: float
    gross_pnl: float
    net_pnl: float
    ev_status: str
    trade_score: float
    verdict: Verdict
    exit_reason: str
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["verdict"] = self.verdict.value
        return d


@dataclass
class Layer1Output:
    trade_result: TradeResult
    trade_score: float
    verdict: Verdict
    is_positive_ev: bool

    def to_dict(self) -> dict:
        return {
            "trade_score": self.trade_score,
            "verdict": self.verdict.value,
            "is_positive_ev": self.is_positive_ev,
            "trade_result": self.trade_result.to_dict(),
        }


@dataclass
class Layer2Output:
    window_id: str
    bot: str
    trades_evaluated: int
    win_rate: float
    avg_r: float
    net_pnl: float
    fee_drag: float
    sharpe_ratio: float
    max_drawdown: float
    trend: Trend
    aggregate_score: float
    verdict: str
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["trend"] = self.trend.value
        return d


@dataclass
class Layer3Output:
    baseline_id: str
    alpha_vs_random: float
    alpha_vs_buyhold: float
    information_ratio: float
    beat_random: bool
    beat_buyhold: bool
    beat_previous: bool
    baseline_score: float
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Layer4Output:
    decision_id: str
    bot: str
    aggregate_score: float
    baseline_score: float
    balance_drawdown: float
    decision: Decision
    action: str
    confidence: float
    recommendation: str
    rollback_to: Optional[str]
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["decision"] = self.decision.value
        return d


@dataclass
class Layer5Output:
    report_id: str
    bot: str
    layer1_summary: dict
    layer2_summary: dict
    layer3_summary: dict
    layer4_summary: dict
    report_text: str
    alert_text: str
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MetaEvalResult:
    meta_eval_id: str
    evaluator_layer: str
    consistency_score: float
    sensitivity_score: float
    stability_score: float
    discrimination_score: float
    timeliness_score: float
    overall_meta_score: float
    trust_level: TrustLevel
    recommendation: str
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["trust_level"] = self.trust_level.value
        return d


@dataclass
class FactorWeights:
    regime: float = 0.25
    flow: float = 0.20
    cross_asset: float = 0.15
    temporal: float = 0.15
    llm_discovered: float = 0.25

    def to_dict(self) -> dict:
        return asdict(self)


class EvaluationDB:
    """SQLite database for persistent evaluation storage."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self._create_tables()

    def _create_tables(self):
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                bot TEXT NOT NULL,
                pair TEXT NOT NULL,
                side TEXT NOT NULL,
                entry REAL, sl REAL, tp REAL, exit_price REAL,
                r_achieved REAL,
                fee_open REAL, fee_close REAL,
                gross_pnl REAL, net_pnl REAL,
                ev_status TEXT, trade_score REAL,
                verdict TEXT, exit_reason TEXT,
                timestamp TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS windows (
                window_id TEXT PRIMARY KEY,
                bot TEXT NOT NULL,
                trades_evaluated INTEGER,
                win_rate REAL, avg_r REAL,
                net_pnl REAL, fee_drag REAL,
                sharpe_ratio REAL, max_drawdown REAL,
                trend TEXT, aggregate_score REAL,
                verdict TEXT, timestamp TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS decisions (
                decision_id TEXT PRIMARY KEY,
                bot TEXT NOT NULL,
                aggregate_score REAL, baseline_score REAL,
                balance_drawdown REAL,
                decision TEXT, action TEXT,
                confidence REAL, recommendation TEXT,
                rollback_to TEXT, timestamp TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS meta_evals (
                meta_eval_id TEXT PRIMARY KEY,
                evaluator_layer TEXT NOT NULL,
                consistency_score REAL, sensitivity_score REAL,
                stability_score REAL, discrimination_score REAL,
                timeliness_score REAL, overall_meta_score REAL,
                trust_level TEXT, recommendation TEXT,
                timestamp TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS factors (
                factor_id TEXT PRIMARY KEY,
                factor_type TEXT NOT NULL,
                factor_name TEXT NOT NULL,
                value REAL, weight REAL,
                adaptive BOOLEAN,
                timestamp TEXT
            )
        """)
        self.conn.commit()

    def save_trade(self, trade: TradeResult):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                trade.trade_id, trade.bot, trade.pair, trade.side,
                trade.entry, trade.sl, trade.tp, trade.exit_price,
                trade.r_achieved, trade.fee_open, trade.fee_close,
                trade.gross_pnl, trade.net_pnl, trade.ev_status,
                trade.trade_score, trade.verdict.value, trade.exit_reason,
                trade.timestamp,
            ),
        )
        self.conn.commit()

    def save_window(self, window: Layer2Output):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO windows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                window.window_id, window.bot, window.trades_evaluated,
                window.win_rate, window.avg_r, window.net_pnl,
                window.fee_drag, window.sharpe_ratio, window.max_drawdown,
                window.trend.value, window.aggregate_score,
                window.verdict, window.timestamp,
            ),
        )
        self.conn.commit()

    def save_decision(self, decision: Layer4Output):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                decision.decision_id, decision.bot, decision.aggregate_score,
                decision.baseline_score, decision.balance_drawdown,
                decision.decision.value, decision.action,
                decision.confidence, decision.recommendation,
                decision.rollback_to, decision.timestamp,
            ),
        )
        self.conn.commit()

    def save_meta_eval(self, meta_eval: MetaEvalResult):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO meta_evals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                meta_eval.meta_eval_id, meta_eval.evaluator_layer,
                meta_eval.consistency_score, meta_eval.sensitivity_score,
                meta_eval.stability_score, meta_eval.discrimination_score,
                meta_eval.timeliness_score, meta_eval.overall_meta_score,
                meta_eval.trust_level.value, meta_eval.recommendation,
                meta_eval.timestamp,
            ),
        )
        self.conn.commit()

    def save_factor(self, factor_id: str, factor_type: str, factor_name: str,
                    value: float, weight: float, adaptive: bool):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO factors VALUES (?,?,?,?,?,?,?)",
            (factor_id, factor_type, factor_name, value, weight, adaptive,
             datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def get_last_n_trades(self, bot: str, n: int) -> List[TradeResult]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM trades WHERE bot = ? ORDER BY timestamp DESC LIMIT ?",
            (bot, n),
        )
        rows = cursor.fetchall()
        trades = []
        for row in rows:
            trades.append(TradeResult(
                trade_id=row[0], bot=row[1], pair=row[2], side=row[3],
                entry=row[4], sl=row[5], tp=row[6], exit_price=row[7],
                r_achieved=row[8], fee_open=row[9], fee_close=row[10],
                gross_pnl=row[11], net_pnl=row[12], ev_status=row[13],
                trade_score=row[14], verdict=Verdict(row[15]),
                exit_reason=row[16], timestamp=row[17],
            ))
        return trades

    def get_all_trades(self, bot: str) -> List[TradeResult]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM trades WHERE bot = ? ORDER BY timestamp DESC",
            (bot,),
        )
        rows = cursor.fetchall()
        trades = []
        for row in rows:
            trades.append(TradeResult(
                trade_id=row[0], bot=row[1], pair=row[2], side=row[3],
                entry=row[4], sl=row[5], tp=row[6], exit_price=row[7],
                r_achieved=row[8], fee_open=row[9], fee_close=row[10],
                gross_pnl=row[11], net_pnl=row[12], ev_status=row[13],
                trade_score=row[14], verdict=Verdict(row[15]),
                exit_reason=row[16], timestamp=row[17],
            ))
        return trades

    def close(self):
        self.conn.close()


class EvaluationEngine:
    """
    Core evaluation engine with full 5-layer pipeline,
    meta-evaluation, DB persistence, and working code.
    """

    def __init__(
        self,
        bot_name: str,
        balance: float = 10.0,
        fee_open: float = 0.0002,
        fee_close: float = 0.0004,
        window_size: int = 20,
        drawdown_pause: float = 0.20,
        drawdown_stop: float = 0.50,
        db_path: str = ":memory:",
    ):
        self.bot_name = bot_name
        self.balance = balance
        self.starting_balance = balance
        self.fee_open = fee_open
        self.fee_close = fee_close
        self.window_size = window_size
        self.drawdown_pause = drawdown_pause
        self.drawdown_stop = drawdown_stop
        self.db = EvaluationDB(db_path)

        self.trades: List[TradeResult] = []
        self.windows: List[Layer2Output] = []
        self.decisions: List[Layer4Output] = []
        self.reports: List[Layer5Output] = []
        self.meta_evals: List[MetaEvalResult] = []
        self._last_good_state: Optional[str] = None

    # ---- Layer 1: Per-Trade Evaluation ----

    def evaluate_trade(self, trade_data: dict) -> Layer1Output:
        r_achieved = trade_data.get("r_achieved", 0.0)
        fee_total = trade_data.get("fee_open", 0.0) + trade_data.get("fee_close", 0.0)
        gross_pnl = trade_data.get("gross_pnl", 0.0)
        net_pnl = gross_pnl - fee_total
        is_positive_ev = net_pnl > 0

        r_score = min(max(r_achieved / 0.50, 0.0), 1.0)
        fee_score = 1.0 if is_positive_ev else 0.3
        ev_score = 1.0 if is_positive_ev else 0.0
        trade_score = r_score * 0.4 + fee_score * 0.3 + ev_score * 0.3

        if r_achieved > 0.05:
            verdict = Verdict.WIN
        elif r_achieved < -0.05:
            verdict = Verdict.LOSS
        else:
            verdict = Verdict.SCRATCH

        result = TradeResult(
            trade_id=trade_data.get("trade_id", ""),
            bot=self.bot_name,
            pair=trade_data.get("pair", ""),
            side=trade_data.get("side", ""),
            entry=trade_data.get("entry", 0.0),
            sl=trade_data.get("sl", 0.0),
            tp=trade_data.get("tp", 0.0),
            exit_price=trade_data.get("exit_price", 0.0),
            r_achieved=r_achieved,
            fee_open=trade_data.get("fee_open", 0.0),
            fee_close=trade_data.get("fee_close", 0.0),
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            ev_status="+EV" if is_positive_ev else "-EV",
            trade_score=round(trade_score, 4),
            verdict=verdict,
            exit_reason=trade_data.get("exit_reason", "unknown"),
        )

        self.trades.append(result)
        self.db.save_trade(result)

        return Layer1Output(
            trade_result=result,
            trade_score=round(trade_score, 4),
            verdict=verdict,
            is_positive_ev=is_positive_ev,
        )

    # ---- Layer 2: Aggregate Evaluation ----

    def _compute_window_metrics(self, trades: List[TradeResult]) -> dict:
        if not trades:
            return {}

        wins = sum(1 for t in trades if t.verdict == Verdict.WIN)
        losses = sum(1 for t in trades if t.verdict == Verdict.LOSS)
        scratches = sum(1 for t in trades if t.verdict == Verdict.SCRATCH)
        total = len(trades)

        win_rate = wins / total if total > 0 else 0.0
        avg_r = sum(t.r_achieved for t in trades) / total if total > 0 else 0.0
        net_pnl = sum(t.net_pnl for t in trades)
        gross_pnl = sum(t.gross_pnl for t in trades)
        total_fees = sum(t.fee_open + t.fee_close for t in trades)
        fee_drag = total_fees / abs(gross_pnl) if gross_pnl != 0 else 0.0

        r_values = [t.r_achieved for t in trades]
        if len(r_values) > 1:
            mean_r = sum(r_values) / len(r_values)
            std_r = (sum((r - mean_r) ** 2 for r in r_values) / len(r_values)) ** 0.5
            sharpe = mean_r / std_r if std_r > 0 else 0.0
        else:
            sharpe = 0.0

        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in trades:
            cumulative += t.net_pnl
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        trend = Trend.STABLE
        if total >= 10:
            first_half_wr = sum(1 for t in trades[: total // 2] if t.verdict == Verdict.WIN) / (total // 2)
            second_half_wr = sum(1 for t in trades[total // 2 :] if t.verdict == Verdict.WIN) / (total - total // 2)
            if second_half_wr - first_half_wr > 0.05:
                trend = Trend.IMPROVING
            elif first_half_wr - second_half_wr > 0.05:
                trend = Trend.DECLINING

        wr_score = min(win_rate / 0.60, 1.0)
        avg_r_score = min(abs(avg_r) / 0.50, 1.0)
        fee_efficiency = 1.0 if net_pnl > 0 else 0.5
        sharpe_score = min(abs(sharpe) / 2.0, 1.0)
        aggregate_score = wr_score * 0.35 + avg_r_score * 0.25 + fee_efficiency * 0.20 + sharpe_score * 0.20

        verdict = "green" if aggregate_score >= 0.60 else ("yellow" if aggregate_score >= 0.40 else "red")

        return {
            "win_rate": round(win_rate, 4),
            "wins": wins, "losses": losses, "scratches": scratches,
            "avg_r": round(avg_r, 4),
            "net_pnl": round(net_pnl, 6),
            "gross_pnl": round(gross_pnl, 6),
            "total_fees": round(total_fees, 6),
            "fee_drag": round(fee_drag, 4),
            "sharpe_ratio": round(sharpe, 4),
            "max_drawdown": round(max_dd, 6),
            "trend": trend.value,
            "aggregate_score": round(aggregate_score, 4),
            "verdict": verdict,
        }

    def run_layer2(self) -> Optional[Layer2Output]:
        if len(self.trades) < self.window_size:
            return None

        window_trades = self.trades[-self.window_size :]
        metrics = self._compute_window_metrics(window_trades)

        window_id = f"{self.bot_name}_window_{len(self.windows) + 1}"
        now = datetime.now(timezone.utc).isoformat()

        output = Layer2Output(
            window_id=window_id,
            bot=self.bot_name,
            trades_evaluated=len(window_trades),
            win_rate=metrics["win_rate"],
            avg_r=metrics["avg_r"],
            net_pnl=metrics["net_pnl"],
            fee_drag=metrics["fee_drag"],
            sharpe_ratio=metrics["sharpe_ratio"],
            max_drawdown=metrics["max_drawdown"],
            trend=Trend(metrics["trend"]),
            aggregate_score=metrics["aggregate_score"],
            verdict=metrics["verdict"],
            timestamp=now,
        )

        self.windows.append(output)
        self.db.save_window(output)
        return output

    # ---- Layer 3: Baseline Comparison ----

    def run_layer3(self, previous_window: Optional[Layer2Output] = None) -> Layer3Output:
        current = self.windows[-1] if self.windows else None
        if not current:
            return Layer3Output(
                baseline_id="N/A",
                alpha_vs_random=0.0, alpha_vs_buyhold=0.0,
                information_ratio=0.0,
                beat_random=False, beat_buyhold=False, beat_previous=False,
                baseline_score=0.0,
            )

        random_wr = 0.50
        random_avg_r = 0.0
        alpha_vs_random = (current.win_rate - random_wr) * 0.5 + (current.avg_r - random_avg_r) * 0.5
        alpha_vs_buyhold = current.net_pnl

        if previous_window and previous_window.avg_r != 0:
            info_ratio = (current.avg_r - previous_window.avg_r) / max(abs(current.avg_r), 0.01)
        else:
            info_ratio = 0.0

        beat_random = alpha_vs_random > 0
        beat_buyhold = alpha_vs_buyhold > 0
        beat_previous = current.avg_r > (previous_window.avg_r if previous_window else 0)

        baseline_score = (
            (1.0 if beat_random else 0.0) * 0.4
            + (1.0 if beat_buyhold else 0.0) * 0.3
            + (1.0 if beat_previous else 0.0) * 0.3
        )

        output = Layer3Output(
            baseline_id=current.window_id,
            alpha_vs_random=round(alpha_vs_random, 4),
            alpha_vs_buyhold=round(alpha_vs_buyhold, 6),
            information_ratio=round(info_ratio, 4),
            beat_random=beat_random,
            beat_buyhold=beat_buyhold,
            beat_previous=beat_previous,
            baseline_score=round(baseline_score, 4),
        )
        return output

    # ---- Layer 4: Decision Gate ----

    def run_layer4(self, layer3: Optional[Layer3Output] = None) -> Layer4Output:
        layer2 = self.windows[-1] if self.windows else None
        if not layer2:
            return Layer4Output(
                decision_id=f"{self.bot_name}_decision_pending",
                bot=self.bot_name,
                aggregate_score=0.0, baseline_score=0.0,
                balance_drawdown=0.0,
                decision=Decision.ITERATE,
                action="waiting for first evaluation window",
                confidence=0.0,
                recommendation="collect more data",
                rollback_to=None,
            )

        l3 = layer3 or Layer3Output(
            baseline_id="N/A", alpha_vs_random=0.0, alpha_vs_buyhold=0.0,
            information_ratio=0.0, beat_random=False, beat_buyhold=False,
            beat_previous=False, baseline_score=0.0,
        )

        current_balance = self.balance
        drawdown = (self.starting_balance - current_balance) / self.starting_balance

        if drawdown >= self.drawdown_stop:
            decision = Decision.STOP
            action = "stop trading, full redesign needed"
            confidence = 1.0
        elif drawdown >= self.drawdown_pause:
            decision = Decision.PAUSE
            action = "pause trading, re-evaluate strategy"
            confidence = 1.0
        elif layer2.aggregate_score >= 0.60 and l3.baseline_score >= 0.40:
            decision = Decision.PERSIST
            action = "keep current strategy, continue trading"
            confidence = 0.78
        elif layer2.aggregate_score >= 0.60 and l3.baseline_score < 0.40:
            decision = Decision.PERSIST
            action = "keep strategy but monitor baseline gap"
            confidence = 0.65
        elif layer2.aggregate_score >= 0.40 and l3.baseline_score >= 0.40:
            decision = Decision.ITERATE
            action = "tweak parameters, test for another 20 trades"
            confidence = 0.60
        elif layer2.aggregate_score < 0.40:
            decision = Decision.ROLLBACK_IMMEDIATE
            action = "immediately revert, pause trading for 1 hour"
            confidence = 0.85
        else:
            decision = Decision.ROLLBACK
            action = "revert to last known good state"
            confidence = 0.70

        output = Layer4Output(
            decision_id=f"{self.bot_name}_decision_{len(self.decisions) + 1}",
            bot=self.bot_name,
            aggregate_score=layer2.aggregate_score,
            baseline_score=l3.baseline_score,
            balance_drawdown=round(drawdown, 4),
            decision=decision,
            action=action,
            confidence=confidence,
            recommendation=action,
            rollback_to=self._last_good_state,
        )

        self.decisions.append(output)
        self.db.save_decision(output)

        if decision == Decision.PERSIST:
            self._last_good_state = layer2.window_id

        return output

    # ---- Layer 5: Report Generator ----

    def run_layer5(self, layer4: Layer4Output, layer3: Optional[Layer3Output] = None) -> Layer5Output:
        layer2 = self.windows[-1] if self.windows else None
        layer1_trades = self.trades[-self.window_size :] if len(self.trades) >= self.window_size else self.trades

        report_lines = []
        report_lines.append(f"EVALUATION REPORT — {self.bot_name.upper()} BOT")
        report_lines.append(f"Window: {layer2.window_id if layer2 else 'N/A'} | Trades: {len(layer1_trades)}")
        if layer2:
            wins = sum(1 for t in layer1_trades if t.verdict == Verdict.WIN)
            losses = sum(1 for t in layer1_trades if t.verdict == Verdict.LOSS)
            report_lines.append(f"WR: {layer2.win_rate*100:.1f}% ({wins}W/{losses}L) | Avg R: +{layer2.avg_r:.3f}")
            report_lines.append(f"Net PnL: ${layer2.net_pnl:+.6f}")
            report_lines.append(f"Score: {layer2.aggregate_score:.2f} ({layer2.verdict.upper()})")
            report_lines.append(f"Trend: {layer2.trend.value}")
        report_lines.append(f"Decision: {layer4.decision.value}")
        report_lines.append(f"Action: {layer4.action}")
        report_lines.append(f"Balance: ${self.balance:.4f} (drawdown: {layer4.balance_drawdown*100:.1f}%)")
        report_text = "\n".join(report_lines)

        alert_lines = []
        alert_lines.append(f"EVALUATION REPORT — {self.bot_name.upper()} Bot")
        if layer2:
            wins = sum(1 for t in layer1_trades if t.verdict == Verdict.WIN)
            losses = sum(1 for t in layer1_trades if t.verdict == Verdict.LOSS)
            alert_lines.append(f"Window: {layer2.window_id} | Trades: {len(layer1_trades)}")
            alert_lines.append(f"WR: {layer2.win_rate*100:.1f}% ({wins}W/{losses}L) | Avg R: +{layer2.avg_r:.3f}")
            alert_lines.append(f"Net PnL: ${layer2.net_pnl:+.6f}")
            alert_lines.append(f"Score: {layer2.aggregate_score:.2f} ({layer2.verdict.upper()})")
        alert_lines.append(f"Decision: {layer4.decision.value}")
        alert_lines.append(f"Balance: ${self.balance:.4f}")
        alert_text = "\n".join(alert_lines)

        output = Layer5Output(
            report_id=f"{self.bot_name}_report_{len(self.reports) + 1}",
            bot=self.bot_name,
            layer1_summary={"trades": len(layer1_trades), "latest": self.trades[-1].to_dict() if self.trades else {}},
            layer2_summary=layer2.to_dict() if layer2 else {},
            layer3_summary=layer3.to_dict() if layer3 else {},
            layer4_summary=layer4.to_dict(),
            report_text=report_text,
            alert_text=alert_text,
        )

        self.reports.append(output)
        return output

    # ---- Meta-Evaluator ----

    def meta_evaluate(self) -> MetaEvalResult:
        """Evaluate the evaluators themselves."""
        if len(self.windows) < 2:
            return MetaEvalResult(
                meta_eval_id=f"meta_{self.bot_name}_pending",
                evaluator_layer="all",
                consistency_score=0.0, sensitivity_score=0.0,
                stability_score=0.0, discrimination_score=0.0,
                timeliness_score=0.0, overall_meta_score=0.0,
                trust_level=TrustLevel.UNKNOWN,
                recommendation="collect more data",
            )

        # Compute consistency: variance of aggregate scores across windows
        scores = [w.aggregate_score for w in self.windows]
        if len(scores) > 1:
            mean_score = sum(scores) / len(scores)
            variance = sum((s - mean_score) ** 2 for s in scores) / len(scores)
            consistency = max(1.0 - variance * 10, 0.0)
        else:
            consistency = 0.5

        # Sensitivity: how much does score change when trades change
        # Use last 2 windows as proxy
        if len(self.windows) >= 2:
            score_change = abs(self.windows[-1].aggregate_score - self.windows[-2].aggregate_score)
            sensitivity = min(score_change * 10, 1.0)
        else:
            sensitivity = 0.5

        # Stability: 1 - coefficient of variation
        if len(scores) > 1 and mean_score > 0:
            std_score = (sum((s - mean_score) ** 2 for s in scores) / len(scores)) ** 0.5
            stability = max(1.0 - (std_score / mean_score), 0.0)
        else:
            stability = 0.5

        # Discrimination: can the evaluator distinguish good from bad trades
        # Use WR as proxy: if WR > 50%, evaluator is discriminating well
        current_wr = self.windows[-1].win_rate if self.windows else 0.0
        discrimination = min(current_wr / 0.60, 1.0)

        # Timeliness: does the evaluator detect degradation early
        # Use trend as proxy: if trend is declining, evaluator should have caught it
        trend_score = {"improving": 1.0, "stable": 0.7, "declining": 0.3}.get(
            self.windows[-1].trend.value, 0.5
        )
        timeliness = trend_score

        overall = (consistency + sensitivity + stability + discrimination + timeliness) / 5.0

        if overall >= 0.75:
            trust = TrustLevel.HIGH
            recommendation = "use_as_primary"
        elif overall >= 0.50:
            trust = TrustLevel.MEDIUM
            recommendation = "use_with_caution"
        elif overall >= 0.25:
            trust = TrustLevel.LOW
            recommendation = "investigate"
        else:
            trust = TrustLevel.UNKNOWN
            recommendation = "send_to_llm_research"

        result = MetaEvalResult(
            meta_eval_id=f"meta_{self.bot_name}_{len(self.meta_evals) + 1}",
            evaluator_layer="all",
            consistency_score=round(consistency, 4),
            sensitivity_score=round(sensitivity, 4),
            stability_score=round(stability, 4),
            discrimination_score=round(discrimination, 4),
            timeliness_score=round(timeliness, 4),
            overall_meta_score=round(overall, 4),
            trust_level=trust,
            recommendation=recommendation,
        )

        self.meta_evals.append(result)
        self.db.save_meta_eval(result)
        return result

    # ---- Dynamic Factor Engine ----

    def update_factors(self, meta_eval: MetaEvalResult) -> Dict[str, float]:
        """Update factor weights based on meta-evaluation."""
        learning_rate = 0.1

        # Base weights
        weights = FactorWeights()

        # Adjust based on meta-evaluation trust level
        if meta_eval.trust_level == TrustLevel.HIGH:
            # Increase all weights slightly
            weights.regime = min(weights.regime * 1.05, 0.50)
            weights.flow = min(weights.flow * 1.05, 0.50)
            weights.cross_asset = min(weights.cross_asset * 1.05, 0.50)
            weights.temporal = min(weights.temporal * 1.05, 0.50)
            weights.llm_discovered = min(weights.llm_discovered * 1.05, 0.50)
        elif meta_eval.trust_level == TrustLevel.LOW:
            # Decrease all weights, increase llm_discovered (research more)
            weights.regime = max(weights.regime * 0.95, 0.05)
            weights.flow = max(weights.flow * 0.95, 0.05)
            weights.cross_asset = max(weights.cross_asset * 0.95, 0.05)
            weights.temporal = max(weights.temporal * 0.95, 0.05)
            weights.llm_discovered = min(weights.llm_discovered * 1.1, 0.50)

        # Save to DB
        self.db.save_factor("f_regime", "regime", "trend_strength", weights.regime, weights.regime, True)
        self.db.save_factor("f_flow", "flow", "cvd_direction", weights.flow, weights.flow, True)
        self.db.save_factor("f_cross_asset", "cross_asset", "btc_trend", weights.cross_asset, weights.cross_asset, True)
        self.db.save_factor("f_temporal", "temporal", "session_regime", weights.temporal, weights.temporal, True)
        self.db.save_factor("f_llm", "llm_discovered", "roll_measure", weights.llm_discovered, weights.llm_discovered, True)

        return weights.to_dict()

    # ---- Unknown Factor Discoverer ----

    def discover_unknowns(self) -> dict:
        """Find what the evaluation system doesn't know."""
        known_not_implemented = ["roll_measure", "vpin", "herding_detection"]
        unknown_discoverable = ["cross_asset_correlation_dynamics", "funding_rate_regimes"]
        unknown_unknowable = ["institutional_order_flow", "dark_pool_activity"]
        known_misunderstood = ["cvd_divergence_veto_only"]

        return {
            "known_not_implemented": known_not_implemented,
            "unknown_discoverable": unknown_discoverable,
            "unknown_unknowable": unknown_unknowable,
            "known_misunderstood": known_misunderstood,
            "research_priority": known_not_implemented,
        }

    # ---- Full Pipeline ----

    def run_pipeline(self) -> dict:
        """Run full 5-layer pipeline + meta-evaluation + factor update."""
        layer2 = self.run_layer2()
        if not layer2:
            return {"status": "waiting_for_window", "trades_so_far": len(self.trades)}

        previous_window = self.windows[-2] if len(self.windows) >= 2 else None
        layer3 = self.run_layer3(previous_window)
        layer4 = self.run_layer4(layer3)
        layer5 = self.run_layer5(layer4, layer3)

        meta_eval = self.meta_evaluate()
        factors = self.update_factors(meta_eval)
        unknowns = self.discover_unknowns()

        return {
            "layer1": None,
            "layer2": layer2.to_dict(),
            "layer3": layer3.to_dict(),
            "layer4": layer4.to_dict(),
            "layer5": layer5.to_dict(),
            "meta_eval": meta_eval.to_dict(),
            "factors": factors,
            "unknowns": unknowns,
            "decision": layer4.decision.value,
        }

    def to_json(self) -> str:
        """Export all evaluation data as JSON."""
        data = {
            "bot_name": self.bot_name,
            "balance": self.balance,
            "starting_balance": self.starting_balance,
            "total_trades": len(self.trades),
            "windows": [w.to_dict() for w in self.windows],
            "decisions": [d.to_dict() for d in self.decisions],
            "reports": [r.to_dict() for r in self.reports],
            "meta_evals": [m.to_dict() for m in self.meta_evals],
            "last_good_state": self._last_good_state,
        }
        return json.dumps(data, indent=2, default=str)

    def close(self):
        self.db.close()