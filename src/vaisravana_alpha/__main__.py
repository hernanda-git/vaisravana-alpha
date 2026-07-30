"""Entrypoint -- build the object graph, then get out of the way.

The legacy `bot_paper.py` was ~1200 lines because it was simultaneously the
entrypoint, the engine, the notifier, the command router, and the wallet.
This module does one thing: construct dependencies in order and hand them to
`AlphaEngine`. Everything it touches is importable and testable without it.

Run with `python -m vaisravana_alpha` or the `vaisravana-alpha` console script.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from vaisravana_alpha import __version__
from vaisravana_alpha.core.params import load_surface
from vaisravana_alpha.core.settings import load_settings
from vaisravana_alpha.engine.runtime import AlphaEngine
from vaisravana_alpha.execution.broker import ModeGuard
from vaisravana_alpha.execution.wallet import PaperWallet
from vaisravana_alpha.notify.telegram import CommandListener, TelegramNotifier
from vaisravana_alpha.storage.db import init_db

log = logging.getLogger("vaisravana_alpha")

COMMANDS = [
    ("status", "engine health, uptime, feed state"),
    ("positions", "open waves and account balance"),
    ("performance", "realized results so far"),
    ("stop", "halt trading and persist the stop flag"),
    ("resume", "clear the stop flag"),
]


def configure_logging(level: str) -> None:
    """Timestamped logs to stdout, so `docker logs` is the single source."""
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-28s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    # These libraries log every request at INFO, which drowns the trade log.
    for noisy in ("httpx", "httpcore", "websockets", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def build_engine() -> tuple[AlphaEngine, CommandListener | None]:
    """Construct the full object graph from the environment."""
    settings = load_settings()
    configure_logging(settings.log_level)

    log.info("Vaiśravaṇa Alpha v%s starting", __version__)
    log.info("mode=%s pairs=%d data=%s",
             settings.mode, len(settings.pairs), settings.data_dir)

    surface = load_surface()
    log.info(
        "surface rev=%d rr=%.2f risk=%.0f%% max_open=%d",
        surface.revision, surface.risk.rr,
        surface.risk.risk_per_trade_pct * 100, surface.risk.max_open_waves,
    )

    conn = init_db(settings.db_path)
    wallet = PaperWallet.from_settings(settings)
    notifier = TelegramNotifier(settings.telegram_token, settings.telegram_chat_id)

    # Paper mode passes no live adapter, so no live broker can be built.
    guard = ModeGuard(mode=settings.mode)
    guard.broker_for(None if settings.is_paper else _live_adapter())

    engine = AlphaEngine(
        settings=settings,
        surface=surface,
        conn=conn,
        notifier=notifier,
        wallet=wallet,
        guard=guard,
    )

    listener = None
    if settings.telegram_listen and notifier.enabled:
        notifier.register_commands(COMMANDS)
        listener = CommandListener(
            notifier=notifier,
            on_command=engine.handle_command,
            allowed_chat_id=settings.telegram_chat_id,
            bot_username=settings.bot_username or None,
        )
    return engine, listener


def _live_adapter():
    """Live trading is not wired. Failing loudly beats a silent paper fallback."""
    raise SystemExit(
        "MODE=live requested but no live exchange adapter is configured. "
        "Refusing to start rather than silently trading in paper."
    )


async def _run() -> int:
    engine, listener = build_engine()
    if listener is not None:
        listener.start()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(
                sig, lambda s=sig: engine.request_stop(f"signal {s.name}")
            )
        except NotImplementedError:
            pass    # not supported on every platform; Ctrl-C still works

    try:
        await engine.run()
    finally:
        if listener is not None:
            listener.stop()
        engine.notifier.close()
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        log.info("interrupted")
        return 130
    except SystemExit as exc:
        log.error("startup refused: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
