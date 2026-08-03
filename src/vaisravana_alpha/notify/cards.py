"""Message cards -- pure formatting, no I/O.

Every function here takes data and returns a string. That makes the whole
notification surface testable without a network, and keeps wording changes
away from delivery logic.

House style, per the owner's preference: a consistent Balance footer on every
trade card (equity, used margin, unrealized, realized), monospace numbers so
columns line up on mobile, and no em dashes.
"""

from __future__ import annotations

from vaisravana_alpha.notify.telegram import html_escape


def _side_icon(side: str) -> str:
    return "🟢" if side == "BUY" else "🔴"


def _balance_footer(wallet) -> list[str]:
    """The four-line account state every trade card ends with."""
    if wallet is None:
        return []
    snap = wallet.snapshot()
    return [
        "",
        "<b>Balance</b>",
        f"<code>  Equity     : {snap['balance']:.4f}$</code>",
        f"<code>  Used       : {snap['used']:.4f}$</code>",
        f"<code>  Unrealized : {snap['unrealized']:+.4f}$</code>",
        f"<code>  Realized   : {snap['realized']:+.4f}$</code>",
    ]


def startup_card(version: str, pairs: list[str], mode: str,
                 balance: float, feed: str) -> str:
    """Boot announcement. Confirms which universe and feed are actually live."""
    return "\n".join([
        f"⚡ <b>Vaiśravaṇa Alpha</b> <code>v{html_escape(version)}</code>",
        "",
        f"<code>  Mode    : {html_escape(mode.upper())}</code>",
        f"<code>  Pairs   : {len(pairs)}</code>",
        f"<code>  Feed    : {html_escape(feed)}</code>",
        f"<code>  Balance : {balance:.4f}$</code>",
        "",
        f"<i>{html_escape(' · '.join(pairs[:8]))}"
        f"{' ...' if len(pairs) > 8 else ''}</i>",
    ])


def wave_open_card(wave, wallet=None, open_fee: float = 0.0) -> str:
    """Entry card. Shows the fee paid up front so cost is never a surprise.

    TP, SL and Entry always show numeric values — never 0.0 and never
    'trailing'. The trade was sized with those parameters; the values
    must be present so the trader can verify the setup in one glance.
    """
    tp = getattr(wave, "tp_price", None) or wave.entry_price
    sl = getattr(wave, "sl_price", None) or wave.entry_price
    lines = [
        f"🌊 <b>OPEN</b> {_side_icon(wave.side)} "
        f"<code>{html_escape(wave.side)} {html_escape(wave.pair)}</code>",
        f"<code>  Entry  : {wave.entry_price:.6f}</code>",
        f"<code>  SL     : {sl:.6f}</code>",
        f"<code>  TP     : {tp:.6f}</code>",
        f"<code>  Size   : {wave.size:.4f}  ({wave.notional:.2f}$ notional)</code>",
        f"<code>  Lev    : {wave.leverage}x  ·  Margin {wave.margin:.4f}$</code>",
        f"<code>  Conf   : {wave.confidence:.2f}</code>",
        f"<code>  Fee    : -{open_fee:.4f}$  (maker)</code>",
    ]
    return "\n".join(lines + _balance_footer(wallet))


def wave_close_card(wave, wallet=None, econ: dict | None = None) -> str:
    """Exit card. Breaks the round trip into gross, fees, and net.

    Showing all three prevents the classic self-deception of reading a
    positive gross as a win when fees made the trade a loser.
    """
    econ = econ or {}
    close_fee = econ.get("close_fee", 0.0)
    open_fee = getattr(wave, "_open_fee", 0.0) or 0.0
    net = econ.get("net", 0.0)
    gross = net + close_fee + open_fee
    icon = "🟢" if net >= 0 else "🔴"
    lines = [
        f"🌊 <b>CLOSE</b> {_side_icon(wave.side)} "
        f"<code>{html_escape(wave.side)} {html_escape(wave.pair)}</code>",
        f"<code>  Exit   : {wave.live_r:+.2f}R  "
        f"({html_escape(wave.close_reason or 'n/a')})</code>",
        f"<code>  Peak   : {wave.peak_r:+.2f}R</code>",
        f"<code>  Gross  : {gross:+.4f}$</code>",
        f"<code>  Fees   : -{open_fee + close_fee:.4f}$  "
        f"(open {open_fee:.4f} + close {close_fee:.4f})</code>",
        f"<code>  {icon} Net : {net:+.4f}$</code>",
    ]
    return "\n".join(lines + _balance_footer(wallet))


def positions_card(waves: list, wallet=None) -> str:
    """Answer to /positions: open exposure plus account state.

    Every position shows full TP/SL so the trader can see
    exactly where each wave would exit — no blanks, no zeros.
    """
    header = [f"🌊 <b>Open positions: {len(waves)}</b>"]
    if wallet is not None:
        snap = wallet.snapshot(waves)
        header += [
            f"<code>  Equity     : {snap['balance']:.4f}$</code>",
            f"<code>  Used       : {snap['used']:.4f}$</code>",
            f"<code>  Free       : {snap['free']:.4f}$</code>",
            f"<code>  Unrealized : {snap['unrealized']:+.4f}$</code>",
            f"<code>  Realized   : {snap['realized']:+.4f}$</code>",
            f"<code>  Peak       : {snap['peak']:.4f}$</code>",
        ]
    if not waves:
        return "\n".join(header + ["", "  <i>scanning, nothing open</i>"])

    rows = []
    for w in waves:
        tp = getattr(w, "tp_price", None) or w.entry_price
        rows.append(
            f"  {_side_icon(w.side)} <b>{html_escape(w.pair)}</b> "
            f"{html_escape(w.side)} ({html_escape(w.tf)})\n"
            f"     E <code>{w.entry_price:.4f}</code>  "
            f"SL <code>{w.sl_price:.4f}</code>  "
            f"TP <code>{tp:.4f}</code>  "
            f"R <code>{w.live_r:+.2f}</code>  "
            f"conf <code>{w.confidence:.2f}</code>"
        )
    return "\n".join(header + [""] + rows)


def performance_card(closed: list, wallet=None) -> str:
    """Answer to /performance: realized results over the closed trades seen.

    Reports the median R alongside the mean, because on a small sample one
    outlier trade can make a losing strategy's average look healthy.
    """
    if not closed:
        return "📊 <b>No closed trades yet.</b>"

    def value(item, attr, default=0.0):
        return item.get(attr, default) if isinstance(item, dict) else getattr(item, attr, default)

    r_values = [float(value(w, "final_r", value(w, "live_r", 0.0)) or 0.0) for w in closed]
    pnl = [float(value(w, "pnl_usd", 0.0) or 0.0) for w in closed]
    # wave_log stores close fee.  Include the open fee recorded in trades when
    # available, so the command reports net economics rather than gross PnL.
    fees = sum(float(value(w, "fees_usd", 0.0) or 0.0) for w in closed)
    gross = sum(float(value(w, "gross_pnl", 0.0) or 0.0) for w in closed)
    wins = sum(1 for r in r_values if r > 0)
    total = len(r_values)
    median_r = sorted(r_values)[total // 2] if r_values else 0.0
    mean_r = (sum(r_values) / total) if total else 0.0

    lines = [
        "📊 <b>Performance</b>",
        f"<code>  Closed   : {total}</code>",
        f"<code>  Win rate : {(wins / total * 100) if total else 0:.1f}%  "
        f"({wins}/{total})</code>",
        f"<code>  Median R : {median_r:+.3f}</code>",
        f"<code>  Mean R   : {mean_r:+.3f}</code>",
        f"<code>  Gross PnL: {gross:+.4f}$</code>",
        f"<code>  Fees     : -{fees:.4f}$</code>",
        f"<code>  Net PnL  : {sum(pnl):+.4f}$</code>",
    ]
    if wallet is not None:
        lines.append(f"<code>  Balance  : {wallet.snapshot()['balance']:.4f}$</code>")
    return "\n".join(lines)


def trades_card(closed: list, wallet=None) -> str:
    """Recent persistent trades for /alpha_trades."""
    if not closed:
        return "📒 <b>No closed trades recorded.</b>"
    lines = [f"📒 <b>Recent trades ({min(len(closed), 12)})</b>"]
    for item in closed[:12]:
        def v(name, default=""):
            return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)
        pnl = float(v("pnl_usd", 0.0) or 0.0)
        icon = "🟢" if pnl >= 0 else "🔴"
        lines.append(
            f"{icon} <code>{html_escape(str(v('side')))} {html_escape(str(v('pair')))}</code> "
            f"<code>{html_escape(str(v('close_reason', 'n/a')))}</code> "
            f"<code>{pnl:+.4f}$</code>"
        )
    if wallet is not None:
        lines += _balance_footer(wallet)
    return "\n".join(lines)


def status_card(version: str, uptime_s: float, pairs: int, open_n: int,
                feed_ok: bool, ticks: int, throttle_cap: int) -> str:
    """Answer to /status: liveness evidence, not just a cheerful 'running'.

    Tick count and feed state are included precisely because a bot can look
    healthy while sitting on a dead feed.
    """
    hours, rem = divmod(int(uptime_s), 3600)
    minutes = rem // 60
    return "\n".join([
        f"⚡ <b>Alpha status</b> <code>v{html_escape(version)}</code>",
        f"<code>  Uptime   : {hours}h {minutes}m</code>",
        f"<code>  Feed     : {'live' if feed_ok else 'DEGRADED'}</code>",
        f"<code>  Ticks    : {ticks}</code>",
        f"<code>  Pairs    : {pairs}</code>",
        f"<code>  Open     : {open_n}</code>",
        f"<code>  Throttle : {throttle_cap}/h</code>",
    ])


def exit_signal_card(signal, wave, wallet=None, price: float = 0.0) -> str:
    """Real-time exit signal card. Shows the regime, factor breakdown,
    salvage, and the action taken. Even more detailed than the entry card
    because the exit decision is where edge is won or lost.

    Includes the fee-aware salvage so the owner can see that an exit that
    looks early was actually the right call once costs are paid.
    """
    f = signal.factors
    w = signal.weights_used
    action_label = {
        "CLOSE_100": "FULL CLOSE",
        "CLOSE_50": "PARTIAL (50%)",
        "HOLD": "HOLD",
        "ADD": "ADD",
        "FLIP": "FLIP BIAS",
    }.get(signal.action.value, signal.action.value)

    icon = {
        "CLOSE_100": "🔴",
        "CLOSE_50": "🟡",
        "HOLD": "🟢",
        "ADD": "🟢",
        "FLIP": "🔄",
    }.get(signal.action.value, "⚪")

    salvage = signal.salvage
    salvage_line = (
        f"<code>  Salvage: {salvage:+.5f}$  "
        f"({'fee-aware ok' if salvage >= 0 else 'below fee line'})</code>"
    )

    lines = [
        f"🎯 <b>REAL-TIME EXIT</b> {icon} <code>{action_label}</code>",
        f"<code>  Pair   : {html_escape(signal.pair)}</code>",
        f"<code>  Side   : {html_escape(wave.side)}</code>",
        f"<code>  Price  : {price:.8f}</code>",
        f"<code>  Conf   : {signal.exit_conf:.3f}</code>",
        f"<code>  Regime : {html_escape(signal.regime.value)}</code>",
        "",
        f"<code>  Factor scores (weight):</code>",
        f"<code>    structural : {f.structural:.2f}  (w={w.get('structural', 0):.2f})</code>",
        f"<code>    momentum   : {f.momentum:.2f}  (w={w.get('momentum', 0):.2f})</code>",
        f"<code>    orderflow  : {f.orderflow:.2f}  (w={w.get('orderflow', 0):.2f})</code>",
        f"<code>    volatility : {f.volatility:.2f}  (w={w.get('volatility', 0):.2f})</code>",
        f"<code>    liquidity  : {f.liquidity:.2f}  (w={w.get('liquidity', 0):.2f})</code>",
        salvage_line,
        f"<code>  Reason : {html_escape(signal.reason[:80])}</code>",
    ]
    return "\n".join(lines + _balance_footer(wallet))


def exit_partial_card(wave, wallet=None, fraction: float = 0.5,
                       price: float = 0.0, econ: dict | None = None) -> str:
    """Partial close card (real-time exit engine banked part of the wave)."""
    econ = econ or {}
    net = econ.get("net", 0.0)
    close_fee = econ.get("close_fee", 0.0)
    icon = "🟡" if net >= 0 else "🔴"
    lines = [
        f"🎯 <b>PARTIAL CLOSE</b> {icon} <code>{wave.side} {html_escape(wave.pair)}</code>",
        f"<code>  Fraction : {fraction * 100:.0f}%</code>",
        f"<code>  Price    : {price:.8f}</code>",
        f"<code>  R        : {wave.live_r:+.2f}R (peak {wave.peak_r:+.2f}R)</code>",
        f"<code>  Fee      : -{close_fee:.5f}$ (close)</code>",
        f"<code>  {icon} Net (part): {net:+.5f}$</code>",
        f"<code>  Remaining: {(1 - fraction) * 100:.0f}% trailing</code>",
    ]
    return "\n".join(lines + _balance_footer(wallet))


def halt_card(reason: str, balance: float) -> str:
    """Shown when the engine stops itself. Always states why."""
    return "\n".join([
        "🛑 <b>Engine halted</b>",
        f"<code>  Reason  : {html_escape(reason)}</code>",
        f"<code>  Balance : {balance:.4f}$</code>",
        "",
        "<i>Send /resume to clear the stop flag.</i>",
    ])
