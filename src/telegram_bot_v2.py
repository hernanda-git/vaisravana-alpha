"""
Telegram Notifier v2 — Redesigned Message Format

Fixes from v1:
- Entry/SL/TP never show 0.0 — show actual price or "pending"
- Fee calculated per trade (open + close) and shown separately
- Balance section always complete: Current, Used, Unrealized, Realized
- All messages informative and complete
- Clean, elegant, professional design
- No 0.0 values for critical fields
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger("vaisravana.notifier.telegram")

_TAG_RE = re.compile(r"<[^>]+>")


def html_escape(text: str) -> str:
    if text is None:
        text = ""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text)


def _fmt_price(v: float, label: str = "") -> str:
    """Format price — never show 0.0, show 'pending' instead."""
    if v is None or v == 0.0:
        return f"{label}<code>pending</code>"
    return f"{label}<code>{v:.8f}</code>"


def _fmt_usd(v: float) -> str:
    """Format USD value."""
    if v is None:
        return "<code>0.0000$</code>"
    return f"<code>{v:+.4f}$</code>"


def _fmt_r(v: float) -> str:
    """Format R multiple."""
    if v is None:
        return "<code>0.00R</code>"
    return f"<code>{v:+.2f}R</code>"


class TelegramNotifier:
    """Redesigned Telegram notifier with clean, informative messages."""

    def __init__(self, bot_token: str, chat_id: str | int):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._base = f"https://api.telegram.org/bot{bot_token}"
        self._client: httpx.Client | None = None
        self._chat_dead: bool = False

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=10)
        return self._client

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        if self._chat_dead:
            return False
        MAX_LEN = 3950
        if len(text) > MAX_LEN:
            text = text[:MAX_LEN - 40] + "\n\n… (truncated)"
        if not self.bot_token:
            log.info("[No bot token] %s", _strip_tags(text)[:100])
            return False
        client = self._get_client()
        resp = client.post(f"{self._base}/sendMessage", json={
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        })
        if resp.status_code == 200:
            return True
        if any(k in resp.text for k in ("chat not found", "bot was kicked",
                                         "bot is not a member", "Forbidden", "PEER_ID")):
            self._chat_dead = True
            log.error("Telegram chat %s unreachable", self.chat_id)
            return False
        if parse_mode != "plain" and "parse entities" in resp.text:
            plain = _strip_tags(text)
            resp2 = client.post(f"{self._base}/sendMessage", json={
                "chat_id": self.chat_id,
                "text": plain,
                "disable_web_page_preview": True,
            })
            if resp2.status_code == 200:
                return True
            log.error("Telegram send failed (plain): %s", resp2.text)
            return False
        log.error("Telegram send failed: %s", resp.text)
        return False

    # ---- Trade Messages ----

    def notify_trade_open(
        self,
        bot_name: str,
        pair: str,
        side: str,
        entry: float,
        sl: float,
        tp: float,
        size: float,
        notional: float,
        leverage: float,
        confidence: float,
        open_fee: float,
        wallet_balance: float,
        used_margin: float,
        unrealized: float,
        realized: float,
    ) -> bool:
        """
        Trade open notification — redesigned for clarity.

        Key fixes:
        - Entry/SL/TP never show 0.0
        - Fee shown separately (open fee only, close fee added at close)
        - Balance section complete
        """
        side_icon = "🟢" if side == "BUY" else "🔴"
        direction = "LONG" if side == "BUY" else "SHORT"

        lines = [
            f"{'═' * 35}",
            f"  {side_icon} <b>{direction} {html_escape(pair)}</b>",
            f"  <b>{html_escape(bot_name)}</b>",
            f"{'═' * 35}",
            "",
            f"  📍 <b>Entry</b>  {_fmt_price(entry, '')}",
            f"  🛑 <b>SL</b>     {_fmt_price(sl, '')}",
            f"  🎯 <b>TP</b>     {_fmt_price(tp, '')}",
            "",
            f"  📊 <b>Trade Details</b>",
            f"  Size       : <code>{size:.4f}</code>",
            f"  Notional   : <code>{notional:.2f}$</code>",
            f"  Leverage   : <code>{leverage}x</code>",
            f"  Confidence : <code>{confidence:.2f}</code>",
            f"  Open Fee   : <code>-{open_fee:.4f}$</code> (maker 0.02%)",
            "",
            f"  💰 <b>Balance</b>",
            f"  Equity     : <code>{wallet_balance:.4f}$</code>",
            f"  Used       : <code>{used_margin:.4f}$</code>",
            f"  Free       : <code>{wallet_balance - used_margin:.4f}$</code>",
            f"  Unrealized : {_fmt_usd(unrealized)}",
            f"  Realized   : {_fmt_usd(realized)}",
            "",
            f"  ⏳ <i>Waiting for TP/SL hit...</i>",
        ]
        return self.send_message("\n".join(lines))

    def notify_trade_close(
        self,
        bot_name: str,
        pair: str,
        side: str,
        exit_price: float,
        exit_reason: str,
        pnl_r: float,
        gross_pnl: float,
        open_fee: float,
        close_fee: float,
        net_pnl: float,
        wallet_balance: float,
        used_margin: float,
        unrealized: float,
        realized: float,
        total_trades: int,
        wins: int,
        losses: int,
        total_fees_paid: float,
    ) -> bool:
        """
        Trade close notification — redesigned for clarity.

        Key fixes:
        - Fee shown separately (open + close)
        - Net PnL includes all fees
        - Balance section complete
        - Win/Loss indicator clear
        """
        is_win = net_pnl >= 0
        result_icon = "🟢" if is_win else "🔴"
        result_text = "WIN" if is_win else "LOSS"
        pnl_icon = "📈" if is_win else "📉"

        # Reason icon
        reason_map = {
            "tp_hit": "🎯",
            "sl_hit": "🛑",
            "max_age": "⏱",
            "bias_flip": "🔄",
            "conf_collapse": "💥",
            "bank_08r": "🏦",
        }
        reason_icon = reason_map.get(exit_reason, "❓")

        total_fee = open_fee + close_fee

        lines = [
            f"{'═' * 35}",
            f"  {result_icon} <b>{result_text}</b> — {html_escape(pair)}",
            f"  {reason_icon} <code>{html_escape(exit_reason)}</code>",
            f"  <b>{html_escape(bot_name)}</b>",
            f"{'═' * 35}",
            "",
            f"  📍 Exit     : <code>{exit_price:.8f}</code>",
            f"  {_fmt_r(pnl_r)}",
            f"  Gross PnL  : {_fmt_usd(gross_pnl)}",
            f"  Fees       : <code>-{total_fee:.4f}$</code>",
            f"    ├ Open   : <code>-{open_fee:.4f}$</code>",
            f"    └ Close  : <code>-{close_fee:.4f}$</code>",
            f"  {pnl_icon} Net PnL  : <code>{net_pnl:+.4f}$</code>",
            "",
            f"  💰 <b>Balance</b>",
            f"  Equity     : <code>{wallet_balance:.4f}$</code>",
            f"  Used       : <code>{used_margin:.4f}$</code>",
            f"  Free       : <code>{wallet_balance - used_margin:.4f}$</code>",
            f"  Unrealized : {_fmt_usd(unrealized)}",
            f"  Realized   : {_fmt_usd(realized)}",
            "",
            f"  📊 <b>Portfolio</b>",
            f"  Trades     : <code>{total_trades}</code>",
            f"  WR         : <code>{wins}/{total_trades} ({wins/total_trades*100:.1f}%)</code>" if total_trades > 0 else "",
            f"  Total Fees : <code>-{total_fees_paid:.4f}$</code>",
        ]

        # Remove empty lines
        lines = [l for l in lines if l != ""]

        return self.send_message("\n".join(lines))

    def notify_evaluation(
        self,
        bot_name: str,
        window_id: str,
        trades_count: int,
        wins: int,
        losses: int,
        win_rate: float,
        avg_r: float,
        net_pnl: float,
        total_fees: float,
        aggregate_score: float,
        verdict: str,
        decision: str,
        confidence: float,
        balance: float,
        drawdown: float,
    ) -> bool:
        """Evaluation report notification."""
        verdict_color = "🟢" if verdict == "green" else "🟡" if verdict == "yellow" else "🔴"
        decision_icon = {
            "persist_changes": "✅",
            "iterate": "🔄",
            "rollback": "⏪",
            "rollback_immediate": "⛔",
            "pause_trading": "⏸",
            "stop_trading": "🛑",
            "research_more": "🔍",
            "investigate_evaluators": "⚠️",
        }.get(decision, "📋")

        lines = [
            f"{'═' * 35}",
            f"  📊 <b>Evaluation Report</b>",
            f"  <b>{html_escape(bot_name)}</b>",
            f"{'═' * 35}",
            "",
            f"  Window     : <code>{window_id}</code>",
            f"  Trades     : <code>{trades_count}</code>",
            f"  Result     : <code>{wins}W / {losses}L</code>",
            f"  WR         : <code>{win_rate * 100:.1f}%</code>",
            f"  Avg R      : {_fmt_r(avg_r)}",
            f"  Net PnL    : {_fmt_usd(net_pnl)}",
            f"  Fees       : <code>-{total_fees:.4f}$</code>",
            f"  Score      : {verdict_color} <code>{aggregate_score:.2f}</code>",
            "",
            f"  {decision_icon} <b>Decision</b>",
            f"  Action     : <code>{decision}</code>",
            f"  Confidence : <code>{confidence:.0%}</code>",
            f"  Drawdown   : <code>{drawdown * 100:.1f}%</code>",
            f"  Balance    : <code>{balance:.4f}$</code>",
        ]
        return self.send_message("\n".join(lines))

    def notify_startup(self, version: str, pairs: list[str], decide_tf: str,
                       ctx_tfs: list[str], cycle_s: int, llm_mode: str,
                       open_n: int) -> bool:
        """Startup notification — redesigned."""
        pair_s = " · ".join(pairs)
        ctx_s = " · ".join(ctx_tfs)
        text = (
            f"{'═' * 35}\n"
            f"  🚀 <b>Bot Started</b>\n"
            f"{'═' * 35}\n"
            f"\n"
            f"  <b>Bot</b>      : <code>vaisravana</code>\n"
            f"  <b>Version</b>  : <code>v{html_escape(version)}</code>\n"
            f"  <b>Mode</b>     : <code>PAPER</code>\n"
            f"  <b>Balance</b> : <code>10.0000$</code>\n"
            f"\n"
            f"  <b>Pairs</b>   : <code>{html_escape(pair_s)}</code>\n"
            f"  <b>Decide TF</b>: <code>{html_escape(decide_tf)}</code>\n"
            f"  <b>Context</b> : <code>{html_escape(ctx_s)}</code>\n"
            f"  <b>Cycle</b>   : <code>{cycle_s}s</code>\n"
            f"  <b>LLM</b>     : <code>{html_escape(llm_mode)}</code>\n"
            f"  <b>Open</b>    : <code>{open_n}</code> positions\n"
        )
        return self.send_message(text)

    def notify_status_30m(self, lines: list[str], overall: str = "",
                          dbline: str = "") -> bool:
        """30m status — redesigned."""
        body = "\n".join(lines) if lines else "<i>Belum ada trade dieksekusi.</i>"
        head = ""
        if overall:
            head += f"{overall}\n"
        if dbline:
            head += f"{dbline}\n"
        if head:
            head += "\n"
        return self.send_message(f"📊 <b>Vessavaṇa - Status (30m)</b>\n\n{head}{body}")

    def notify_health_check(self, version: str, region: str, open_n: int,
                            feed_ok: bool = True, notes: str = "") -> bool:
        """Health check — redesigned."""
        status = "SEHAT" if feed_ok else "FEED BERMASALAH"
        text = (
            f"{'═' * 35}\n"
            f"  <b>Vessavaṇa</b> · <code>v{html_escape(version)}</code>\n"
            f"{'═' * 35}\n"
            f"\n"
            f"  Status     : <code>{status}</code>\n"
            f"  Region     : <code>{html_escape(region)}</code>\n"
            f"  Positions  : <code>{open_n}</code> open\n"
            f"  Uptime     : <i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</i>\n"
        )
        if notes:
            text += f"\n  <i>{html_escape(notes)}</i>\n"
        return self.send_message(text)

    def notify_kill_switch(self, reason: str) -> bool:
        text = (
            f"{'═' * 35}\n"
            f"  🛑 <b>KILL-SWITCH TRIPPED</b>\n"
            f"{'═' * 35}\n"
            f"\n"
            f"  <i>{html_escape(reason)}</i>\n"
            f"\n"
            f"  Paper loop halted — no further entries.\n"
        )
        return self.send_message(text)

    def notify_db_stats(self, version: str, stats: dict) -> bool:
        """DB stats — redesigned."""
        o = stats.get("overall", {})
        c = stats.get("counts", {})
        wr = o.get("win_rate_pct", 0.0)
        text = (
            f"🗄️ <b>Database</b> · <code>v{html_escape(version)}</code>\n"
            f"\n"
            f"  WR         : <code>{wr:.1f}%</code> "
            f"({o.get('n_wins', 0)}W / {o.get('n_losses', 0)}L · {o.get('n_closed', 0)} closed)\n"
            f"  DB Size    : <code>{html_escape(stats.get('size_human', '0 B'))}</code>\n"
            f"  Total Row  : <code>{stats.get('total_rows', 0)}</code>\n"
            f"\n"
            f"  trade_logs     : <code>{c.get('trade_logs', 0)}</code>\n"
            f"  decisions_log  : <code>{c.get('decisions_log', 0)}</code>\n"
            f"  results_log    : <code>{c.get('results_log', 0)}</code>\n"
            f"  exec_events    : <code>{c.get('exec_events', 0)}</code>\n"
            f"  system_health  : <code>{c.get('system_health', 0)}</code>\n"
        )
        return self.send_message(text)


class TelegramCommandListener:
    """Polls Telegram getUpdates in a daemon thread and dispatches slash commands."""

    def __init__(self, notifier: "TelegramNotifier",
                 on_command: "callable[[str, str], None]",
                 poll_s: int = 2, allowed_chat_id: "str | int | None" = None,
                 bot_username: "str | None" = None) -> None:
        self._n = notifier
        self._on = on_command
        self._poll_s = poll_s
        self._allowed = str(allowed_chat_id) if allowed_chat_id not in (None, "", "0") else None
        self._bot_username = bot_username.lstrip("@").lower() if bot_username else None
        self._offset = 0
        self._stop = threading.Event()
        self._thread: "threading.Thread | None" = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as e:
                log.debug("tg command poll error: %s", e)
            self._stop.wait(self._poll_s)

    def _poll_once(self) -> None:
        client = self._n._get_client()
        try:
            resp = client.get(f"{self._n._base}/getUpdates",
                              params={"offset": self._offset, "timeout": 1})
        except Exception as e:
            log.debug("tg getUpdates failed: %s", e)
            return
        if resp.status_code != 200:
            return
        try:
            data = resp.json()
        except Exception:
            return
        for upd in data.get("result", []):
            self._offset = upd.get("update_id", self._offset) + 1
            msg = upd.get("message") or upd.get("edited_message") or {}
            chat_id = msg.get("chat", {}).get("id")
            if self._allowed is not None and str(chat_id) != self._allowed:
                continue
            text = (msg.get("text") or "").strip()
            if not text.startswith("/"):
                continue
            head = text.split()[0].lower()
            target = head.split("@", 1)[1] if "@" in head else None
            if target is not None and self._bot_username is not None and target != self._bot_username:
                continue
            try:
                self._on(text, text)
            except Exception as e:
                log.exception("tg command handler error: %s", e)