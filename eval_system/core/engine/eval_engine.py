"""
Autonomous Multi-Layer Evaluation Engine
Core orchestration for 5-layer evaluation pipeline.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


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
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Layer1Output:
    trade_result: TradeResult
    trade_score: float
    verdict: Verdict
    is_positive_ev: bool


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
    timestamp: str


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
    timestamp: str


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
    timestamp: str


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
    timestamp: str


class EvaluationEngine:
    def __init__(
        self,
        bot_name: str,
        balance: float = 10.0,
        fee_open: float = 0.0002,
        fee_close: float = 0.0004,
        window_size: int = 20,
        drawdown_pause: float = 0.20,
        drawdown_stop: float = 0.50,
    ):
        self.bot_name = bot_name
        self.balance = balance
        self.starting_balance = balance
        self.fee_open = fee_open
        self.fee_close = fee_close
        self.window_size = window_size
        self.drawdown_pause = drawdown_pause
        self.drawdown_stop = drawdown_stop

        self.trades: list[TradeResult] = []
        self.windows: list[Layer2Output] = []
        self.decisions: list[Layer4Output] = []
        self.reports: list[Layer5Output] = []
        self._last_good_state: Optional[str] = None

    def evaluate_trade(self, trade_data: dict) -> Layer1Output:
        """Layer 1: Per-trade evaluation."""
        r_achieved = trade_data.get("r_achieved", 0.0)
        fee_total = trade_data.get("fee_open", 0.0) + trade_data.get("fee_close", 0.0)
        gross_pnl = trade_data.get("gross_pnl", 0.0)
        net_pnl = gross_pnl - fee_total

        is_positive_ev = net_pnl > 0

        # Trade score: weighted combination
        r_score = min(max(r_achieved / 0.50, 0.0), 1.0)  # normalized to 0.5R target
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

        return Layer1Output(
            trade_result=result,
            trade_score=round(trade_score, 4),
            verdict=verdict,
            is_positive_ev=is_positive_ev,
        )

    def _compute_window_metrics(self, trades: list[TradeResult]) -> dict:
        """Compute aggregate metrics for a window of trades."""
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

        # Sharpe ratio (simplified: mean / std of R values)
        r_values = [t.r_achieved for t in trades]
        if len(r_values) > 1:
            mean_r = sum(r_values) / len(r_values)
            std_r = (sum((r - mean_r) ** 2 for r in r_values) / len(r_values)) ** 0.5
            sharpe = mean_r / std_r if std_r > 0 else 0.0
        else:
            sharpe = 0.0

        # Max drawdown
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

        # Trend: compare last 10 trades WR vs first 10 trades WR
        trend = Trend.STABLE
        if total >= 10:
            first_half_wr = sum(1 for t in trades[: total // 2] if t.verdict == Verdict.WIN) / (total // 2)
            second_half_wr = sum(1 for t in trades[total // 2 :] if t.verdict == Verdict.WIN) / (total - total // 2)
            if second_half_wr - first_half_wr > 0.05:
                trend = Trend.IMPROVING
            elif first_half_wr - second_half_wr > 0.05:
                trend = Trend.DECLINING

        # Aggregate score
        wr_score = min(win_rate / 0.60, 1.0)
        avg_r_score = min(abs(avg_r) / 0.50, 1.0)
        fee_efficiency = 1.0 if net_pnl > 0 else 0.5
        sharpe_score = min(abs(sharpe) / 2.0, 1.0)
        aggregate_score = wr_score * 0.35 + avg_r_score * 0.25 + fee_efficiency * 0.20 + sharpe_score * 0.20

        verdict = "green" if aggregate_score >= 0.60 else ("yellow" if aggregate_score >= 0.40 else "red")

        return {
            "win_rate": round(win_rate, 4),
            "wins": wins,
            "losses": losses,
            "scratches": scratches,
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
        """Layer 2: Aggregate evaluation over window."""
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
        return output

    def run_layer3(self, previous_window: Optional[Layer2Output] = None) -> Layer3Output:
        """Layer 3: Baseline comparison."""
        current = self.windows[-1] if self.windows else None
        if not current:
            return Layer3Output(
                baseline_id="N/A",
                alpha_vs_random=0.0,
                alpha_vs_buyhold=0.0,
                information_ratio=0.0,
                beat_random=False,
                beat_buyhold=False,
                beat_previous=False,
                baseline_score=0.0,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        # Alpha vs random: assume random has 50% WR, 0R avg
        random_wr = 0.50
        random_avg_r = 0.0
        alpha_vs_random = (current.win_rate - random_wr) * 0.5 + (current.avg_r - random_avg_r) * 0.5

        # Alpha vs buy-hold: assume buy-hold has 0% return over short window
        alpha_vs_buyhold = current.net_pnl

        # Information ratio vs previous window
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
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        return output

    def run_layer4(self, layer3: Optional[Layer3Output] = None) -> Layer4Output:
        """Layer 4: Decision gate."""
        layer2 = self.windows[-1] if self.windows else None
        if not layer2:
            return Layer4Output(
                decision_id=f"{self.bot_name}_decision_pending",
                bot=self.bot_name,
                aggregate_score=0.0,
                baseline_score=0.0,
                balance_drawdown=0.0,
                decision=Decision.ITERATE,
                action="waiting for first evaluation window",
                confidence=0.0,
                recommendation="collect more data",
                rollback_to=None,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        l3 = layer3 or Layer3Output(
            baseline_id="N/A",
            alpha_vs_random=0.0,
            alpha_vs_buyhold=0.0,
            information_ratio=0.0,
            beat_random=False,
            beat_buyhold=False,
            beat_previous=False,
            baseline_score=0.0,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        current_balance = self.balance
        drawdown = (self.starting_balance - current_balance) / self.starting_balance

        # Decision logic
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
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        self.decisions.append(output)

        if decision == Decision.PERSIST:
            self._last_good_state = layer2.window_id

        return output

    def run_layer5(self, layer4: Layer4Output) -> Layer5Output:
        """Layer 5: Report generator."""
        layer2 = self.windows[-1] if self.windows else None
        layer1_trades = self.trades[-self.window_size :] if len(self.trades) >= self.window_size else self.trades

        # Build report text
        report_lines = []
        report_lines.append(f"EVALUATION REPORT — {self.bot_name.upper()} BOT")
        report_lines.append(f"Window: {layer2.window_id if layer2 else 'N/A'} | Trades: {len(layer1_trades)}")
        if layer2:
            wins = sum(1 for t in layer1_trades if t.verdict == Verdict.WIN)
            losses = sum(1 for t in layer1_trades if t.verdict == Verdict.LOSS)
            report_lines.append(f"WR: {layer2.win_rate*100:.1f}% ({wins}W/{losses}L) | Avg R: +{layer2.avg_r:.3f}")
            report_lines.append(f"Net PnL: ${layer2.net_pnl:+.6f} | Fees: ${layer2.total_fees:+.6f}")
            report_lines.append(f"Score: {layer2.aggregate_score:.2f} ({layer2.verdict.upper()})")
            report_lines.append(f"Trend: {layer2.trend.value}")
        report_lines.append(f"Decision: {layer4.decision.value}")
        report_lines.append(f"Action: {layer4.action}")
        report_lines.append(f"Balance: ${self.balance:.4f} (drawdown: {layer4.balance_drawdown*100:.1f}%)")

        report_text = "\n".join(report_lines)

        # Build alert text (compact for Telegram)
        alert_lines = []
        alert_lines.append(f"EVALUATION REPORT — {self.bot_name.upper()} Bot")
        if layer2:
            wins = sum(1 for t in layer1_trades if t.verdict == Verdict.WIN)
            losses = sum(1 for t in layer1_trades if t.verdict == Verdict.LOSS)
            alert_lines.append(f"Window: {layer2.window_id} | Trades: {len(layer1_trades)}")
            alert_lines.append(f"WR: {layer2.win_rate*100:.1f}% ({wins}W/{losses}L) | Avg R: +{layer2.avg_r:.3f}")
            alert_lines.append(f"Net PnL: ${layer2.net_pnl:+.6f} | Fees: ${layer2.total_fees:+.6f}")
            alert_lines.append(f"Score: {layer2.aggregate_score:.2f} ({layer2.verdict.upper()})")
        alert_lines.append(f"Decision: {layer4.decision.value}")
        alert_lines.append(f"Balance: ${self.balance:.4f}")

        alert_text = "\n".join(alert_lines)

        output = Layer5Output(
            report_id=f"{self.bot_name}_report_{len(self.reports) + 1}",
            bot=self.bot_name,
            layer1_summary={"trades": len(layer1_trades), "latest": self.trades[-1].__dict__ if self.trades else {}},
            layer2_summary=layer2.__dict__ if layer2 else {},
            layer3_summary=layer3.__dict__ if layer3 else {},
            layer4_summary=layer4.__dict__,
            report_text=report_text,
            alert_text=alert_text,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        self.reports.append(output)
        return output

    def run_pipeline(self) -> dict:
        """Run full 5-layer pipeline."""
        layer1_results = [self.evaluate_trade(t.__dict__) for t in self.trades[-1:]] if self.trades else []
        layer2 = self.run_layer2()
        layer3 = self.run_layer3(self.windows[-2] if len(self.windows) >= 2 else None)
        layer4 = self.run_layer4(layer3)
        layer5 = self.run_layer5(layer4)

        return {
            "layer1": layer1_results[-1].__dict__ if layer1_results else None,
            "layer2": layer2.__dict__ if layer2 else None,
            "layer3": layer3.__dict__,
            "layer4": layer4.__dict__,
            "layer5": layer5.__dict__,
        }

    def to_json(self) -> str:
        """Export all evaluation data as JSON."""
        data = {
            "bot_name": self.bot_name,
            "balance": self.balance,
            "starting_balance": self.starting_balance,
            "total_trades": len(self.trades),
            "windows": [w.__dict__ for w in self.windows],
            "decisions": [d.__dict__ for d in self.decisions],
            "reports": [r.__dict__ for r in self.reports],
            "last_good_state": self._last_good_state,
        }
        return json.dumps(data, indent=2, default=str)