"""
Visualization Utilities for Evaluation System v2.

Provides text-based visualization of evaluation results
for terminal output and report generation.
"""

from typing import List, Dict, Any


def render_evaluation_report(report: Dict[str, Any]) -> str:
    """Render evaluation report as formatted text."""
    lines = []
    lines.append("=" * 60)
    lines.append("  EVALUATION REPORT")
    lines.append("=" * 60)
    lines.append("")

    # Layer 2 summary
    layer2 = report.get("layer2", {})
    if layer2:
        lines.append(f"  Window: {layer2.get('window_id', 'N/A')}")
        lines.append(f"  Trades: {layer2.get('trades_evaluated', 0)}")
        lines.append(f"  WR: {layer2.get('win_rate', 0) * 100:.1f}%")
        lines.append(f"  Avg R: +{layer2.get('avg_r', 0):.3f}")
        lines.append(f"  Net PnL: ${layer2.get('net_pnl', 0):+.6f}")
        lines.append(f"  Score: {layer2.get('aggregate_score', 0):.2f} ({layer2.get('verdict', 'N/A').upper()})")
        lines.append(f"  Trend: {layer2.get('trend', 'N/A')}")
        lines.append("")

    # Layer 3 summary
    layer3 = report.get("layer3", {})
    if layer3:
        lines.append(f"  vs Random: {layer3.get('alpha_vs_random', 0):+.4f}")
        lines.append(f"  vs Buy-Hold: ${layer3.get('alpha_vs_buyhold', 0):+.6f}")
        lines.append(f"  Baseline Score: {layer3.get('baseline_score', 0):.2f}")
        lines.append("")

    # Layer 4 decision
    layer4 = report.get("layer4", {})
    if layer4:
        lines.append(f"  Decision: {layer4.get('decision', 'N/A')}")
        lines.append(f"  Confidence: {layer4.get('confidence', 0):.2f}")
        lines.append(f"  Action: {layer4.get('action', 'N/A')}")
        lines.append("")

    # Meta-evaluation
    meta_eval = report.get("meta_eval", {})
    if meta_eval:
        lines.append(f"  Meta Trust: {meta_eval.get('trust_level', 'unknown')}")
        lines.append(f"  Overall Score: {meta_eval.get('overall_meta_score', 0):.2f}")
        lines.append(f"  Consistency: {meta_eval.get('consistency_score', 0):.2f}")
        lines.append(f"  Sensitivity: {meta_eval.get('sensitivity_score', 0):.2f}")
        lines.append(f"  Stability: {meta_eval.get('stability_score', 0):.2f}")
        lines.append(f"  Discrimination: {meta_eval.get('discrimination_score', 0):.2f}")
        lines.append(f"  Timeliness: {meta_eval.get('timeliness_score', 0):.2f}")
        lines.append("")

    # Factors
    factors = report.get("factors", {})
    if factors:
        lines.append("  Factor Weights:")
        for name, weight in factors.items():
            bar = "█" * int(weight * 20)
            lines.append(f"    {name:20s} {weight:.2f} {bar}")
        lines.append("")

    # Unknowns
    unknowns = report.get("unknowns", {})
    if unknowns:
        lines.append("  Unknown Factor Discovery:")
        for category in ["known_not_implemented", "unknown_discoverable", "known_misunderstood"]:
            items = unknowns.get(category, [])
            if items:
                lines.append(f"    {category}: {', '.join(items)}")
        lines.append("")

    # Research results
    research = report.get("research_results", {})
    if research:
        lines.append("  LLM Research Results:")
        for factor, info in research.items():
            lines.append(f"    {factor}: {info.get('action', 'N/A')} [{info.get('priority', 'N/A')}]")
        lines.append("")

    lines.append("=" * 60)

    return "\n".join(lines)


def render_walk_forward_report(wf_result: Dict[str, Any]) -> str:
    """Render walk-forward analysis as formatted text."""
    lines = []
    lines.append("=" * 60)
    lines.append("  WALK-FORWARD ANALYSIS")
    lines.append("=" * 60)
    lines.append("")

    if wf_result.get("status") != "complete":
        lines.append(f"  Status: {wf_result.get('status')}")
        lines.append(f"  Total Trades: {wf_result.get('total_trades', 0)}")
        return "\n".join(lines)

    lines.append(f"  Total Trades: {wf_result.get('total_trades', 0)}")
    lines.append(f"  Windows: {wf_result.get('num_windows', 0)}")
    lines.append(f"  Overall WR: {wf_result.get('overall_wr', 0) * 100:.1f}%")
    lines.append(f"  Overall Avg R: +{wf_result.get('overall_avg_r', 0):.3f}")
    lines.append(f"  Overall PnL: ${wf_result.get('overall_pnl', 0):+.6f}")
    lines.append(f"  WR Stability (CV): {wf_result.get('wr_cv', 0):.2f}")
    lines.append(f"  Best Window: {wf_result.get('best_window')} (WR: {wf_result.get('best_wr', 0) * 100:.1f}%)")
    lines.append(f"  Worst Window: {wf_result.get('worst_window')} (WR: {wf_result.get('worst_wr', 0) * 100:.1f}%)")
    lines.append("")

    # Regimes
    regimes = wf_result.get("regimes", [])
    if regimes:
        lines.append("  Market Regimes Detected:")
        for r in regimes:
            lines.append(f"    {r['type']}: windows {r['start_window']}-{r['end_window']} "
                        f"(WR: {r['avg_wr'] * 100:.1f}%, Score: {r['avg_score']:.2f})")
        lines.append("")

    # Window-by-window
    windows = wf_result.get("windows", [])
    if windows:
        lines.append("  Window Details:")
        for w in windows:
            lines.append(f"    {w['window_id']}: WR={w['win_rate']*100:.1f}% "
                        f"AvgR=+{w['avg_r']:.3f} PnL=${w['net_pnl']:+.6f} "
                        f"Score={w['aggregate_score']:.2f}")
        lines.append("")

    lines.append("=" * 60)

    return "\n".join(lines)


def render_telegram_alert(report: Dict[str, Any]) -> str:
    """Render compact Telegram alert."""
    layer2 = report.get("layer2", {})
    layer4 = report.get("layer4", {})
    meta = report.get("meta_eval", {})

    lines = []
    lines.append(f"EVALUATION — {layer2.get('bot', 'N/A').upper()}")
    lines.append(f"WR: {layer2.get('win_rate', 0) * 100:.1f}% | AvgR: +{layer2.get('avg_r', 0):.3f}")
    lines.append(f"PnL: ${layer2.get('net_pnl', 0):+.6f} | Score: {layer2.get('aggregate_score', 0):.2f}")
    lines.append(f"Decision: {layer4.get('decision', 'N/A')}")
    lines.append(f"Trust: {meta.get('trust_level', 'unknown')} | Confidence: {report.get('confidence', 0):.0%}")

    return "\n".join(lines)