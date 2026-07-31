"""
Telegram Notifier v3 — Redesigned Appearance

Visual redesign for clean, elegant, professional messages.

Changes from v2:
- Unified card structure with header/body/footer
- Consistent emoji placement (left side, never inline)
- Better spacing and visual hierarchy
- Section dividers with unicode box-drawing chars
- Color-coded borders (green for win, red for loss, blue for info)
- Compact but informative — no wasted space
- All critical data visible at a glance
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


def _fmt_price(v: float) -> str:
    if v is None or v == 0.0:
        return "<code>—</code>"
    return f"<code>{v:.8f}</code>"


def _fmt_usd(v: float) -> str:
    if v is None:
        return "<code>0.0000$</code>"
    return f"<code>{v:+.4f}$</code>"


def _fmt_r(v: float) -> str:
    if v is None:
        return "<code>0.00R</code>"
    return f"<code>{v:+.2f}R</code>"


def _icon(condition: bool, true_icon: str, false_icon: str = "") -> str:
    return true_icon if condition else false_icon


class TelegramNotifier:
    """Redesigned Telegram notifier with clean, elegant appearance."""

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

    # ── Helper: build a card ──────────────────────────────────────────

    def _card(
        self,
        header_emoji: str,
        header_text: str,
        body_lines: list[str],
        footer_lines: list[str] | None = None,
    ) -> str:
        """Build a unified card structure."""
        lines = [
            f"{'╔' + '═' * 34 + '╗'}",
            f"║  {header_emoji}  <b>{header_text}</b>",
            f"{'╠' + '═' * 34 + '╣'}",
        ]
        for line in body_lines:
            lines.append(f"║  {line}")
        if footer_lines:
            lines.append(f"{'╠' + '═' * 34 + '╣'}")
            for line in footer_lines:
                lines.append(f"║  {line}")
        lines.append(f"{'╚' + '═' * 34 + '╝'}")
        return "\n".join(lines)

    # ── Trade Open ────────────────────────────────────────────────────

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
        side_icon = "🟢" if side == "BUY" else "🔴"
        direction = "LONG" if side == "BUY" else "SHORT"

        body = [
            f"{side_icon} <b>{direction}</b> — <code>{html_escape(pair)}</code>",
            f"",
            f"📍 Entry  {_fmt_price(entry)}",
            f"🛑 SL     {_fmt_price(sl)}",
            f"🎯 TP     {_fmt_price(tp)}",
            f"",
            f"📊 Size      <code>{size:.4f}</code>",
            f"📊 Notional  <code>{notional:.2f}$</code>",
            f"📊 Leverage  <code>{leverage}x</code>",
            f"📊 Confidence <code>{confidence:.2f}</code>",
            f"",
            f"💸 Open Fee  <code>-{open_fee:.4f}$</code>",
        ]

        footer = [
            f"💰 Equity   <code>{wallet_balance:.4f}$</code>",
            f"💰 Used     <code>{used_margin:.4f}$</code>",
            f"💰 Free     <code>{wallet_balance - used_margin:.4f}$</code>",
            f"📈 Unreal   {_fmt_usd(unrealized)}",
            f"📈 Realized {_fmt_usd(realized)}",
            f"",
            f"⏳ Waiting for TP/SL...",
        ]

        return self.send_message(self._card("🌊", f"{bot_name} — Trade Open", body, footer))

    # ── Trade Close ───────────────────────────────────────────────────

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
        is_win = net_pnl >= 0
        result_icon = "🟢" if is_win else "🔴"
        result_text = "WIN" if is_win else "LOSS"
        pnl_icon = "📈" if is_win else "📉"

        reason_map = {
            "tp_hit": "🎯 TP Hit",
            "sl_hit": "🛑 SL Hit",
            "max_age": "⏱ Max Age",
            "bias_flip": "🔄 Bias Flip",
            "conf_collapse": "💥 Conf Collapse",
            "bank_08r": "🏦 Bank 0.8R",
        }
        reason_text = reason_map.get(exit_reason, f"❓ {exit_reason}")

        total_fee = open_fee + close_fee

        body = [
            f"{result_icon} <b>{result_text}</b> — <code>{html_escape(pair)}</code>",
            f"",
            f"📍 Exit     {_fmt_price(exit_price)}",
            f"📊 R        {_fmt_r(pnl_r)}",
            f"📊 Gross    {_fmt_usd(gross_pnl)}",
            f"",
            f"💸 Fees:",
            f"   Open     <code>-{open_fee:.4f}$</code>",
            f"   Close    <code>-{close_fee:.4f}$</code>",
            f"   Total    <code>-{total_fee:.4f}$</code>",
            f"",
            f"{pnl_icon} Net      <code>{net_pnl:+.4f}$</code>",
        ]

        footer = [
            f"💰 Equity   <code>{wallet_balance:.4f}$</code>",
            f"💰 Used     <code>{used_margin:.4f}$</code>",
            f"💰 Free     <code>{wallet_balance - used_margin:.4f}$</code>",
            f"📈 Unreal   {_fmt_usd(unrealized)}",
            f"📈 Realized {_fmt_usd(realized)}",
            f"",
            f"📊 WR: <code>{wins}/{total_trades} ({wins/total_trades*100:.1f}%)</code>" if total_trades > 0 else "",
            f"📊 Total Fees: <code>-{total_fees_paid:.4f}$</code>",
        ]

        footer = [l for l in footer if l != ""]

        return self.send_message(self._card(reason_text.split(" ")[0], f"{bot_name} — Trade Close", body, footer))

    # ── Evaluation Report ─────────────────────────────────────────────

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
        verdict_color = "🟢" if verdict == "green" else "🟡" if verdict == "yellow" else "🔴"
        decision_icon = {
            "persist_changes": "✅ Persist",
            "iterate": "🔄 Iterate",
            "rollback": "⏪ Rollback",
            "rollback_immediate": "⛔ Rollback Now",
            "pause_trading": "⏸ Pause",
            "stop_trading": "🛑 Stop",
            "research_more": "🔍 Research",
            "investigate_evaluators": "⚠️ Investigate",
        }.get(decision, "📋 Pending")

        body = [
            f"📊 <b>Window</b> — <code>{window_id}</code>",
            f"",
            f"📈 Trades     <code>{trades_count}</code>",
            f"📈 WR         <code>{wins}W / {losses}L ({win_rate * 100:.1f}%)</code>",
            f"📈 Avg R      {_fmt_r(avg_r)}",
            f"📈 Net PnL    {_fmt_usd(net_pnl)}",
            f"💸 Fees       <code>-{total_fees:.4f}$</code>",
            f"",
            f"{verdict_color} Score     <code>{aggregate_score:.2f}</code>",
        ]

        footer = [
            f"📋 Decision  {decision_icon}",
            f"📋 Confidence <code>{confidence:.0%}</code>",
            f"📋 Drawdown  <code>{drawdown * 100:.1f}%</code>",
            f"📋 Balance   <code>{balance:.4f}$</code>",
        ]

        return self.send_message(self._card("📊", f"{bot_name} — Evaluation", body, footer))

    # ── Startup ───────────────────────────────────────────────────────

    def notify_startup(self, version: str, pairs: list[str], decide_tf: str,
                       ctx_tfs: list[str], cycle_s: int, llm_mode: str,
                       open_n: int) -> bool:
        pair_s = " · ".join(pairs)
        ctx_s = " · ".join(ctx_tfs)

        body = [
            f"🚀 <b>Bot Started</b>",
            f"",
            f"🤖 Bot       <code>vaisravana</code>",
            f"📦 Version   <code>v{html_escape(version)}</code>",
            f"💰 Mode      <code>PAPER</code>",
            f"💰 Balance   <code>10.0000$</code>",
            f"",
            f"📍 Pairs     <code>{html_escape(pair_s)}</code>",
            f"📍 Decide TF <code>{html_escape(decide_tf)}</code>",
            f"📍 Context   <code>{html_escape(ctx_s)}</code>",
            f"📍 Cycle     <code>{cycle_s}s</code>",
            f"📍 LLM       <code>{html_escape(llm_mode)}</code>",
            f"📍 Open      <code>{open_n}</code> positions",
        ]

        return self.send_message(self._card("🚀", "Bot Startup", body))

    # ── Status 30m ────────────────────────────────────────────────────

    def notify_status_30m(self, lines: list[str], overall: str = "",
                          dbline: str = "") -> bool:
        body = lines if lines else ["<i>Belum ada trade dieksekusi.</i>"]
        head = []
        if overall:
            head.append(overall)
        if dbline:
            head.append(dbline)
        if head:
            head.append("")

        return self.send_message(self._card("📊", "Status (30m)", head + body))

    # ── Health Check ──────────────────────────────────────────────────

    def notify_health_check(self, version: str, region: str, open_n: int,
                            feed_ok: bool = True, notes: str = "") -> bool:
        status = "🟢 SEHAT" if feed_ok else "🔴 FEED BERMASALAH"

        body = [
            f"📡 Status    {status}",
            f"🌍 Region    <code>{html_escape(region)}</code>",
            f"📂 Positions <code>{open_n}</code> open",
            f"⏰ Uptime    <i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</i>",
        ]

        if notes:
            body.append(f"")
            body.append(f"<i>{html_escape(notes)}</i>")

        return self.send_message(self._card("💚", "Health Check", body))

    # ── Kill Switch ───────────────────────────────────────────────────

    def notify_kill_switch(self, reason: str) -> bool:
        body = [
            f"🛑 <b>Kill-Switch Tripped</b>",
            f"",
            f"<i>{html_escape(reason)}</i>",
            f"",
            f"Paper loop halted — no further entries.",
        ]
        return self.send_message(self._card("🛑", "Kill-Switch", body))

    # ── DB Stats ──────────────────────────────────────────────────────

    def notify_db_stats(self, version: str, stats: dict) -> bool:
        o = stats.get("overall", {})
        c = stats.get("counts", {})
        wr = o.get("win_rate_pct", 0.0)

        body = [
            f"🗄️ <b>Database</b>",
            f"",
            f"📈 WR       <code>{wr:.1f}%</code> "
            f"({o.get('n_wins', 0)}W / {o.get('n_losses', 0)}L · {o.get('n_closed', 0)} closed)",
            f"💾 Size     <code>{html_escape(stats.get('size_human', '0 B'))}</code>",
            f"📊 Rows     <code>{stats.get('total_rows', 0)}</code>",
            f"",
            f"📋 trade_logs     <code>{c.get('trade_logs', 0)}</code>",
            f"📋 decisions_log  <code>{c.get('decisions_log', 0)}</code>",
            f"📋 results_log    <code>{c.get('results_log', 0)}</code>",
            f"📋 exec_events    <code>{c.get('exec_events', 0)}</code>",
            f"📋 system_health  <code>{c.get('system_health', 0)}</code>",
        ]

        return self.send_message(self._card("🗄️", "Database Stats", body))


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