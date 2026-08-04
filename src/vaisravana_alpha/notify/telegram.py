"""Telegram transport -- sending messages and receiving slash commands.

Deliberately small. The legacy notifier carried fourteen `notify_*` helpers
inherited from a different bot, most of which the wave engine never called.
This module keeps the transport concerns only -- HTML escaping, truncation,
the plain-text fallback, and the dead-chat latch -- while message *content*
lives in `cards.py`, so changing how a card reads never risks breaking
delivery.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Callable

import httpx

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")

# Telegram hard-caps a message at 4096 chars; leave headroom for the notice.
MAX_MESSAGE_LEN = 3950


def html_escape(text: str) -> str:
    """Escape the three characters that break Telegram's HTML parse mode."""
    if text is None:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def strip_tags(text: str) -> str:
    """Drop HTML tags -- used for the plain-text retry."""
    return _TAG_RE.sub("", text)


class TelegramNotifier:
    """Sends messages to one chat, with graceful degradation.

    Failure handling is the interesting part. A malformed-entities error
    retries as plain text rather than dropping the message, and a permanent
    chat error (bot kicked, wrong chat id) latches `_chat_dead` so the bot
    stops hammering the API every tick for the rest of its life.
    """

    def __init__(self, bot_token: str, chat_id: str | int,
                 enabled: bool = True) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._enabled = enabled
        self._base = f"https://api.telegram.org/bot{bot_token}"
        self._client: httpx.Client | None = None
        self._chat_dead = False

    @property
    def enabled(self) -> bool:
        """False when no token is configured -- the engine then logs instead."""
        return self._enabled and bool(self.bot_token) and not self._chat_dead

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=10)
        return self._client

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send one message. Returns True only on confirmed delivery."""
        if self._chat_dead:
            return False
        if len(text) > MAX_MESSAGE_LEN:
            text = text[: MAX_MESSAGE_LEN - 40] + "\n\n... (truncated)"
        if not self.bot_token:
            log.info("[telegram disabled] %s", strip_tags(text)[:120])
            return False

        client = self._get_client()
        try:
            resp = client.post(
                f"{self._base}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                },
            )
        except Exception as exc:
            log.warning("telegram send failed (network): %s", exc)
            return False

        if resp.status_code == 200:
            return True

        permanent = ("chat not found", "bot was kicked", "bot is not a member",
                     "Forbidden", "PEER_ID")
        if any(marker in resp.text for marker in permanent):
            self._chat_dead = True
            log.error(
                "telegram chat %s is unreachable (%s) -- notifications disabled "
                "until restart", self.chat_id, resp.text[:100],
            )
            return False

        if parse_mode != "plain" and "parse entities" in resp.text:
            try:
                retry = client.post(
                    f"{self._base}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": strip_tags(text),
                        "disable_web_page_preview": True,
                    },
                )
                if retry.status_code == 200:
                    return True
                log.error("telegram plain-text retry failed: %s", retry.text[:200])
            except Exception as exc:
                log.warning("telegram plain-text retry failed: %s", exc)
            return False

        log.error("telegram send failed: %s", resp.text[:200])
        return False

    def register_commands(self, commands: list[tuple[str, str]]) -> bool:
        """Publish the slash-command list so Telegram shows autocomplete."""
        if not self.bot_token:
            return False
        payload = [{"command": c, "description": d} for c, d in commands]
        try:
            resp = self._get_client().post(
                f"{self._base}/setMyCommands", json={"commands": payload}, timeout=10,
            )
            return resp.status_code == 200 and resp.json().get("ok", False)
        except Exception as exc:
            log.debug("register_commands failed: %s", exc)
            return False

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


class CommandListener:
    """Polls `getUpdates` on a daemon thread and dispatches slash commands.

    Two filters matter when several bots share a chat. `allowed_chat_id`
    ignores anything from another chat, and `bot_username` honours only
    commands explicitly addressed to this bot (`/status@my_bot`), so a
    sibling bot's `/stop` cannot halt this one. Bare commands still work for
    the common single-bot setup.
    """

    def __init__(
        self,
        notifier: TelegramNotifier,
        on_command: Callable[[str], None],
        poll_s: float = 2.0,
        allowed_chat_id: str | int | None = None,
        bot_username: str | None = None,
    ) -> None:
        self._notifier = notifier
        self._on_command = on_command
        self._poll_s = poll_s
        self._allowed = (
            str(allowed_chat_id) if allowed_chat_id not in (None, "", "0") else None
        )
        self._bot_username = bot_username.lstrip("@").lower() if bot_username else None
        self._offset = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="telegram-listener", daemon=True,
        )
        self._thread.start()
        log.info("telegram command listener started")

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                # Polling must never kill the process -- a transient API blip
                # would otherwise take the whole bot down with it.
                log.debug("telegram poll error: %s", exc)
            self._stop.wait(self._poll_s)

    def healthy(self) -> bool:
        """Return whether Telegram API access and command polling are usable."""
        try:
            resp = self._notifier._get_client().get(
                f"{self._notifier._base}/getMe", timeout=10,
            )
            return resp.status_code == 200 and bool(resp.json().get("ok"))
        except Exception:
            return False

    def _poll_once(self) -> None:
        client = self._notifier._get_client()
        try:
            resp = client.get(
                f"{self._notifier._base}/getUpdates",
                params={"offset": self._offset, "timeout": 1},
            )
        except Exception as exc:
            log.debug("getUpdates failed: %s", exc)
            return
        if resp.status_code != 200:
            log.warning("telegram getUpdates HTTP %s: %s", resp.status_code, resp.text[:160])
            return
        try:
            updates = resp.json().get("result", [])
        except Exception:
            return

        for update in updates:
            self._offset = update.get("update_id", self._offset) + 1
            msg = update.get("message") or update.get("edited_message") or {}
            chat_id = msg.get("chat", {}).get("id")
            if self._allowed is not None and str(chat_id) != self._allowed:
                continue
            text = (msg.get("text") or "").strip()
            if not text.startswith("/"):
                continue
            log.info("telegram command received: %s chat=%s", text.split()[0], chat_id)
            head = text.split()[0].lower()
            target = head.split("@", 1)[1] if "@" in head else None
            if target and self._bot_username and target != self._bot_username:
                continue    # addressed to a different bot in the same chat
            try:
                self._on_command(text)
            except Exception as exc:
                log.exception("command handler raised on %r: %s", text, exc)
