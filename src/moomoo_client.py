"""Sync data from a local moomoo OpenD gateway via the moomoo OpenAPI.

The moomoo SDK is imported lazily inside functions so the rest of the dashboard
runs even when `moomoo-api` is not installed (manual-entry mode).

Prerequisites for syncing:
  * moomoo OpenD running and logged in (default 127.0.0.1:11111).
  * Trading unlocked manually in the OpenD GUI (the SDK cannot unlock it).
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import date, timedelta

from .config import Config
from . import wheel
from .options import parse_option_code, underlying_of


class MoomooUnavailable(RuntimeError):
    """Raised when the moomoo SDK isn't installed."""


class OpenDUnreachable(RuntimeError):
    """Raised when nothing is listening on the OpenD host/port.

    Important: the moomoo SDK's trade context retries forever (and floods logs)
    if it can't connect, so we probe the TCP port first and bail out fast.
    """


def _opend_listening(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _require_opend(cfg: Config) -> None:
    if not _opend_listening(cfg.moomoo.host, cfg.moomoo.port):
        raise OpenDUnreachable(
            f"No moomoo OpenD found at {cfg.moomoo.host}:{cfg.moomoo.port}. "
            "Start OpenD, log in, and unlock trading, then try again."
        )


@dataclass
class SyncResult:
    deals: list[dict]
    positions: list[dict]
    account: dict | None
    messages: list[str]


def _sdk():
    try:
        import moomoo  # noqa: WPS433  (lazy, optional dependency)
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise MoomooUnavailable(
            "moomoo SDK not installed. Run `pip install moomoo-api` to enable sync."
        ) from exc
    return moomoo


def _enum(moomoo, name: str, member: str):
    return getattr(getattr(moomoo, name), member)


def _norm_side(trd_side: str) -> str:
    side = str(trd_side).upper()
    return "sell" if "SELL" in side else "buy"


def _open_trade_ctx(moomoo, cfg: Config):
    return moomoo.OpenSecTradeContext(
        filter_trdmarket=_enum(moomoo, "TrdMarket", cfg.moomoo.trd_market),
        host=cfg.moomoo.host,
        port=cfg.moomoo.port,
        security_firm=_enum(moomoo, "SecurityFirm", cfg.moomoo.security_firm),
    )


def test_connection(cfg: Config) -> tuple[bool, str]:
    """Quick connectivity check. Returns (ok, message)."""
    try:
        moomoo = _sdk()
    except MoomooUnavailable as exc:
        return False, str(exc)
    if not _opend_listening(cfg.moomoo.host, cfg.moomoo.port):
        return False, (
            f"No moomoo OpenD found at {cfg.moomoo.host}:{cfg.moomoo.port}. "
            "Start OpenD, log in, and unlock trading, then try again."
        )
    try:
        ctx = _open_trade_ctx(moomoo, cfg)
        trd_env = _enum(moomoo, "TrdEnv", cfg.moomoo.trd_env)
        ret, data = ctx.get_acc_list()
        ctx.close()
        if ret != moomoo.RET_OK:
            return False, f"OpenD reachable but query failed: {data}"
        return True, f"Connected. {len(data)} account(s) visible ({cfg.moomoo.trd_env})."
    except Exception as exc:  # pragma: no cover - network/runtime dependent
        return False, f"Could not reach OpenD at {cfg.moomoo.host}:{cfg.moomoo.port} ({exc})."


def _deal_row_to_dict(row, cfg: Config) -> dict:
    code = str(row.get("code", ""))
    contract = parse_option_code(code)
    sec_type = "option" if contract else "stock"
    side = _norm_side(row.get("trd_side"))
    qty = abs(float(row.get("qty", 0) or 0))
    price = float(row.get("price", 0) or 0)

    if sec_type == "option":
        opt_type = contract.opt_type
        strike = contract.strike
        expiry = contract.expiry.isoformat()
        premium = wheel.gross_premium(side, qty, price)
        fee = cfg.fees.per_contract * qty
    else:
        opt_type = None
        strike = None
        expiry = None
        premium = 0.0
        fee = 0.0

    return {
        "deal_id": str(row.get("deal_id")),
        "order_id": str(row.get("order_id", "")),
        "code": code,
        "underlying": underlying_of(code),
        "sec_type": sec_type,
        "opt_type": opt_type,
        "strike": strike,
        "expiry": expiry,
        "side": side,
        "qty": qty,
        "price": price,
        "premium": premium,
        "fee": fee,
        "trade_time": str(row.get("create_time", "")),
        "source": "moomoo",
        "wheel_event": wheel.classify_event(side, sec_type, opt_type),
        "cycle_id": None,
        "status": str(row.get("deal_status", row.get("status", ""))),
        "notes": None,
    }


def _position_row_to_dict(row) -> dict:
    code = str(row.get("code", ""))
    qty = float(row.get("qty", 0) or 0)
    if str(row.get("position_side", "")).upper() == "SHORT":
        qty = -abs(qty)
    return {
        "code": code,
        "underlying": underlying_of(code),
        "sec_type": "option" if parse_option_code(code) else "stock",
        "qty": qty,
        "cost_price": float(row.get("cost_price", 0) or 0),
        "market_val": float(row.get("market_val", 0) or 0),
        "pl_val": float(row.get("pl_val", 0) or 0),
        "pl_ratio": float(row.get("pl_ratio", 0) or 0),
        "updated_at": date.today().isoformat(),
    }


def sync(cfg: Config) -> SyncResult:
    """Pull deals, positions, and account info from OpenD."""
    moomoo = _sdk()
    _require_opend(cfg)
    messages: list[str] = []
    trd_env = _enum(moomoo, "TrdEnv", cfg.moomoo.trd_env)
    ctx = _open_trade_ctx(moomoo, cfg)

    deals: list[dict] = []
    positions: list[dict] = []
    account: dict | None = None
    try:
        # --- Historical deals ---
        start = (date.today() - timedelta(days=cfg.moomoo.history_days)).isoformat()
        end = date.today().isoformat()
        # Note: history_deal_list_query has no refresh_cache parameter (unlike
        # position_list_query / accinfo_query) in the moomoo SDK.
        ret, data = ctx.history_deal_list_query(
            start=start, end=end, trd_env=trd_env
        )
        if ret == moomoo.RET_OK:
            deals = [_deal_row_to_dict(row, cfg) for _, row in data.iterrows()]
            messages.append(f"Pulled {len(deals)} deals since {start}.")
        else:
            messages.append(f"Deal query failed: {data}")

        # --- Current positions ---
        ret, data = ctx.position_list_query(trd_env=trd_env, refresh_cache=True)
        if ret == moomoo.RET_OK:
            positions = [_position_row_to_dict(row) for _, row in data.iterrows()]
            messages.append(f"Pulled {len(positions)} open positions.")
        else:
            messages.append(f"Position query failed: {data}")

        # --- Account funds ---
        # currency defaults to HKD in the SDK; request the dashboard currency.
        ret, data = ctx.accinfo_query(
            trd_env=trd_env, refresh_cache=True, currency=cfg.app.currency
        )
        if ret == moomoo.RET_OK and len(data):
            r = data.iloc[0]
            account = {
                "net_asset": float(r.get("total_assets", 0) or 0),
                "cash": float(r.get("cash", 0) or 0),
                "power": float(r.get("power", 0) or 0),
            }
            messages.append("Pulled account info.")
        else:
            messages.append(f"Account query failed: {data}")
    finally:
        ctx.close()

    return SyncResult(deals=deals, positions=positions, account=account, messages=messages)
