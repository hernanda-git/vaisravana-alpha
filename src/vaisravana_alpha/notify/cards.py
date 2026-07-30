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
    """Entry card. Shows the fee paid up front so cost is never a surprise."""
    tp = getattr(wave, "tp_price", None)
    lines = [
        f"🌊 <b>OPEN</b> {_side_icon(wave.side)} "
        f"<code>{html_escape(wave.side)} {html_escape(wave.pair)}</code>",
        f"<code>  Entry  : {wave.entry_price:.6f}</code>",
        f"<code>  SL     : {wave.sl_price:.6f}</code>",
        f"<code>  TP     : {f'{tp:.6f}' if tp else 'trailing'}</code>",
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
    """Answer to /positions: open exposure plus account state."""
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
        rows.append(
            f"  {_side_icon(w.side)} <b>{html_escape(w.pair)}</b> "
            f"{html_escape(w.side)} ({html_escape(w.tf)})\n"
            f"     E <code>{w.entry_price:.4f}</code>  "
            f"SL <code>{w.sl_price:.4f}</code>  "
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

    r_values = [w.live_r for w in closed if w.live_r != 0]
    pnl = [getattr(w, "pnl_usd", 0.0) or 0.0 for w in closed]
    fees = sum(getattr(w, "fees_usd", 0.0) or 0.0 for w in closed)
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
        f"<code>  Net PnL  : {sum(pnl):+.4f}$</code>",
        f"<code>  Fees     : -{fees:.4f}$</code>",
    ]
    if wallet is not None:
        lines.append(f"<code>  Balance  : {wallet.snapshot()['balance']:.4f}$</code>")
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


def halt_card(reason: str, balance: float) -> str:
    """Shown when the engine stops itself. Always states why."""
    return "\n".join([
        "🛑 <b>Engine halted</b>",
        f"<code>  Reason  : {html_escape(reason)}</code>",
        f"<code>  Balance : {balance:.4f}$</code>",
        "",
        "<i>Send /resume to clear the stop flag.</i>",
    ])
