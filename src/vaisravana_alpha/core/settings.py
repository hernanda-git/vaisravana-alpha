"""Runtime settings — one typed, documented registry for every env var.

The legacy codebase read `os.getenv` from a dozen modules, so nobody could
answer "what knobs exist?" without grepping. This module is the single
answer. Every tunable is declared once, with its type, default, and the
layer that consumes it.

Two prefixes are accepted for each key: the new `ALPHA_` and the legacy
`VAISRAVANA_`. `ALPHA_` wins when both are set, so an existing .env keeps
working while new deployments migrate at their own pace.

Nothing here imports from other layers -- settings sit at the bottom of the
graph alongside models.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, fields

log = logging.getLogger(__name__)

_NEW_PREFIX = "ALPHA_"
_OLD_PREFIX = "VAISRAVANA_"


def _raw(key: str) -> str | None:
    """Read `ALPHA_<key>`, falling back to the legacy `VAISRAVANA_<key>`."""
    val = os.getenv(_NEW_PREFIX + key)
    if val is not None and val != "":
        return val
    val = os.getenv(_OLD_PREFIX + key)
    if val is not None and val != "":
        return val
    return None


def env_str(key: str, default: str) -> str:
    val = _raw(key)
    return default if val is None else val


def env_int(key: str, default: int) -> int:
    val = _raw(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        log.warning("env %s=%r is not an int, using default %d", key, val, default)
        return default


def env_float(key: str, default: float) -> float:
    val = _raw(key)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        log.warning("env %s=%r is not a float, using default %s", key, val, default)
        return default


def env_bool(key: str, default: bool) -> bool:
    val = _raw(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def env_list(key: str, default: list[str]) -> list[str]:
    val = _raw(key)
    if val is None:
        return list(default)
    return [p.strip() for p in val.split(",") if p.strip()]


_DEFAULT_PAIRS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "1000PEPEUSDT", "1000BONKUSDT",
    "ENAUSDT", "WLDUSDT", "PENGUUSDT", "AAVEUSDT", "TAOUSDT",
    "INJUSDT", "APEUSDT", "PUMPUSDT", "WIFUSDT", "CRVUSDT",
]


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the runtime configuration.

    Built once at boot by `load_settings()` and passed down explicitly. No
    module should call `os.getenv` for anything declared here.
    """

    # -- identity / mode ---------------------------------------------------
    mode: str = "paper"                 # paper | live. live needs human approval.
    data_dir: str = "/data"             # wallet, DB, and stop-flag live here
    log_level: str = "INFO"

    # -- universe ----------------------------------------------------------
    pairs: list[str] = field(default_factory=lambda: list(_DEFAULT_PAIRS))
    decision_tf: str = "1m"
    context_tfs: list[str] = field(default_factory=lambda: ["1m", "5m", "15m"])

    # -- market data -------------------------------------------------------
    ws_url: str = "wss://fstream.binance.com/ws"   # STREAM host, not fapi (403)
    rest_base: str = "https://fapi.binance.com"
    rest_poll_s: float = 5.0            # REST safety-net poll interval
    ws_settle_s: float = 10.0           # grace period before REST net starts
    proxy_url: str = ""                 # HTTPS_PROXY passthrough, empty = direct

    # -- paper wallet ------------------------------------------------------
    paper_balance: float = 10.0         # starting fake balance in USD
    paper_stop_at: float = 0.0          # halt when balance drops to this
    fee_open: float = 0.0002            # maker 0.02% on open
    fee_close: float = 0.0004           # taker 0.04% on close
    risk_pct: float = 0.20              # fraction of balance risked per wave

    # -- engine pacing -----------------------------------------------------
    warmup_s: float = 90.0              # no opens until context is seeded
    max_open_waves: int = 8
    max_wave_age_s: int = 900

    # -- notifications -----------------------------------------------------
    telegram_token: str = ""
    telegram_chat_id: str = ""
    telegram_listen: bool = True
    bot_username: str = ""

    # ---------------------------------------------------------------------

    @property
    def is_paper(self) -> bool:
        return self.mode == "paper"

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "vaisravana-alpha.db")

    @property
    def wallet_path(self) -> str:
        return os.path.join(self.data_dir, "paper_wallet.json")

    @property
    def stop_flag_path(self) -> str:
        return os.path.join(self.data_dir, "alpha_stop.flag")

    def redacted(self) -> dict:
        """Settings as a dict, with secrets masked -- safe to log."""
        out = {}
        for f in fields(self):
            val = getattr(self, f.name)
            if "token" in f.name and val:
                val = f"<set:{len(val)} chars>"
            out[f.name] = val
        return out


def load_settings() -> Settings:
    """Build a Settings snapshot from the environment.

    Raises SystemExit on a configuration that cannot possibly work, so the
    process dies at boot with a clear message instead of silently misbehaving
    an hour later.
    """
    mode = env_str("MODE", "paper").lower()
    if mode not in ("paper", "live"):
        raise SystemExit(f"MODE must be 'paper' or 'live', got {mode!r}")

    settings = Settings(
        mode=mode,
        data_dir=env_str("DATA", "/data"),
        log_level=env_str("LOG_LEVEL", "INFO").upper(),
        pairs=env_list("PAIRS", _DEFAULT_PAIRS),
        decision_tf=env_str("DECISION_TF", "1m"),
        context_tfs=env_list("CONTEXT_TFS", ["1m", "5m", "15m"]),
        ws_url=env_str("BINANCE_WS_URL", "wss://fstream.binance.com/ws"),
        rest_base=env_str("BINANCE_REST_BASE", "https://fapi.binance.com"),
        rest_poll_s=env_float("REST_POLL_S", 5.0),
        ws_settle_s=env_float("WS_SETTLE_S", 10.0),
        proxy_url=os.getenv("HTTPS_PROXY", ""),
        paper_balance=env_float("PAPER_BALANCE", 10.0),
        paper_stop_at=env_float("PAPER_STOP", 0.0),
        fee_open=env_float("PAPER_FEE_OPEN", 0.0002),
        fee_close=env_float("PAPER_FEE_CLOSE", 0.0004),
        risk_pct=env_float("PAPER_RISK_PCT", 0.20),
        warmup_s=env_float("WARMUP_S", 90.0),
        max_open_waves=env_int("MAX_OPEN_WAVES", 8),
        max_wave_age_s=env_int("MAX_WAVE_AGE_S", 900),
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("NOTIFY_CHAT_ID", ""),
        telegram_listen=env_bool("CMD_LISTEN", True),
        bot_username=env_str("BOT_USERNAME", ""),
    )

    if not settings.pairs:
        raise SystemExit("PAIRS resolved to an empty universe")
    if settings.paper_balance <= 0:
        raise SystemExit(f"PAPER_BALANCE must be > 0, got {settings.paper_balance}")
    if settings.fee_open < 0 or settings.fee_close < 0:
        raise SystemExit("fee rates cannot be negative")
    if settings.mode == "live":
        log.warning("MODE=live -- every entry still requires explicit human approval")

    return settings
