"""Wheel-strategy domain logic.

Turns raw fills (``deals``) into:
  * per-option **legs** with realized P&L, outcome, and return-on-capital, and
  * per-underlying **wheel cycles** (CSP -> assignment -> CC -> called away).

The functions here are pure (no DB, no moomoo) so they are easy to unit-test.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from .db import OPTION_MULTIPLIER

# wheel_event values
SELL_PUT = "sell_put"
SELL_CALL = "sell_call"
BUY_TO_CLOSE = "buy_to_close"
ASSIGNED = "assigned"
EXPIRED = "expired"
STOCK_BUY = "stock_buy"
STOCK_SELL = "stock_sell"


def gross_premium(side: str, qty: float, price: float,
                  multiplier: int = OPTION_MULTIPLIER) -> float:
    """Signed option cash flow: positive credit when sold, negative when bought."""
    sign = 1 if side == "sell" else -1
    return sign * abs(qty) * price * multiplier


def _matched_stock_pnl(stk: pd.DataFrame) -> float:
    """Realized stock P&L for one ticker via average cost.

    Only shares actually sold (called away) are realized; shares still held are
    NOT counted as a loss — their value lives in unrealized P&L instead.
    """
    if stk.empty:
        return 0.0
    buys = stk[stk["side"] == "buy"]
    sells = stk[stk["side"] == "sell"]
    buy_qty = buys["qty"].sum()
    sell_qty = sells["qty"].sum()
    if sell_qty <= 0:
        return 0.0  # nothing sold yet -> nothing realized
    if buy_qty <= 0:
        # Sold without a tracked buy (rare); treat proceeds as realized.
        return float((sells["qty"] * sells["price"]).sum())
    avg_cost = (buys["qty"] * buys["price"]).sum() / buy_qty
    sell_avg = (sells["qty"] * sells["price"]).sum() / sell_qty
    matched = min(sell_qty, buy_qty)
    return float(matched * (sell_avg - avg_cost))


def realized_stock_pnl(deals: pd.DataFrame) -> float:
    """Total realized stock P&L across tickers (assigned shares that were sold)."""
    if deals.empty:
        return 0.0
    stocks = deals[deals["sec_type"] == "stock"]
    return float(sum(_matched_stock_pnl(grp) for _, grp in stocks.groupby("underlying")))


def classify_event(side: str, sec_type: str, opt_type: str | None) -> str:
    """Best-effort wheel event from a fill's basic attributes.

    Assignment vs. plain expiry/stock-trade can't be known from one fill alone;
    those are refined by :func:`build_option_legs` and user confirmation.
    """
    if sec_type == "option":
        if side == "sell":
            return SELL_PUT if opt_type == "P" else SELL_CALL
        return BUY_TO_CLOSE
    return STOCK_BUY if side == "buy" else STOCK_SELL


def _today(as_of: date | None) -> pd.Timestamp:
    return pd.Timestamp(as_of or date.today())


def build_option_legs(deals: pd.DataFrame, as_of: date | None = None) -> pd.DataFrame:
    """Aggregate option fills into one row per option contract (``code``).

    Columns: underlying, role (CSP/CC), opt_type, strike, expiry, contracts,
    open_time, close_time, premium_collected, fees, realized_pnl, status,
    days_held, capital, roc, annualized_roc, won.
    """
    cols = ["underlying", "role", "opt_type", "strike", "expiry", "code",
            "contracts", "open_time", "close_time", "premium_collected", "fees",
            "realized_pnl", "status", "days_held", "capital", "roc",
            "annualized_roc", "won"]
    if deals.empty:
        return pd.DataFrame(columns=cols)

    options = deals[deals["sec_type"] == "option"].copy()
    if options.empty:
        return pd.DataFrame(columns=cols)

    now = _today(as_of)
    legs = []
    for code, grp in options.groupby("code"):
        sells = grp[grp["side"] == "sell"]
        buys = grp[grp["side"] == "buy"]
        sell_qty = sells["qty"].sum()
        buy_qty = buys["qty"].sum()
        opt_type = grp["opt_type"].iloc[0]
        strike = float(grp["strike"].iloc[0])
        expiry = grp["expiry"].iloc[0]
        contracts = sell_qty if sell_qty else buy_qty

        premium_collected = grp["premium"].sum()      # signed credit - debits
        fees = grp["fee"].fillna(0).sum()
        open_time = sells["trade_time"].min() if not sells.empty else grp["trade_time"].min()
        close_time = buys["trade_time"].max() if not buys.empty else pd.NaT

        tagged_assigned = (grp["wheel_event"] == ASSIGNED).any()
        is_past_expiry = pd.notna(expiry) and expiry < now

        if buy_qty >= sell_qty and sell_qty > 0:
            status = "closed"          # bought to close
        elif tagged_assigned:
            status = "assigned"
            close_time = expiry
        elif is_past_expiry:
            status = "expired"         # assumed worthless (kept full credit)
            close_time = expiry
        else:
            status = "open"

        role = "CSP" if opt_type == "P" else "CC"
        capital = strike * OPTION_MULTIPLIER * contracts

        if status == "open":
            realized_pnl = float("nan")
            end = now
            won = float("nan")
        else:
            realized_pnl = premium_collected - fees
            end = close_time if pd.notna(close_time) else now
            won = 1.0 if realized_pnl > 0 else 0.0

        days_held = max((end - open_time).days, 0) if pd.notna(open_time) else 0
        net_credit = premium_collected - fees
        roc = (net_credit / capital) if capital else float("nan")
        annualized_roc = (roc * 365 / days_held) if days_held > 0 else float("nan")

        legs.append({
            "underlying": grp["underlying"].iloc[0],
            "role": role,
            "opt_type": opt_type,
            "strike": strike,
            "expiry": expiry,
            "code": code,
            "contracts": contracts,
            "open_time": open_time,
            "close_time": close_time,
            "premium_collected": premium_collected,
            "fees": fees,
            "realized_pnl": realized_pnl,
            "status": status,
            "days_held": days_held,
            "capital": capital,
            "roc": roc,
            "annualized_roc": annualized_roc,
            "won": won,
        })

    return pd.DataFrame(legs, columns=cols).sort_values("open_time").reset_index(drop=True)


def suggest_assignments(deals: pd.DataFrame, tol: float = 0.01) -> pd.DataFrame:
    """Heuristic: short option legs whose strike matches a stock fill near expiry.

    Returns candidate (option code, stock deal_id) pairs the user can confirm.
    A short put assigned -> a stock BUY at strike; a short call -> a stock SELL.
    """
    cols = ["option_code", "underlying", "opt_type", "strike", "stock_deal_id",
            "stock_side", "stock_price", "stock_time"]
    if deals.empty:
        return pd.DataFrame(columns=cols)

    options = deals[deals["sec_type"] == "option"]
    stocks = deals[deals["sec_type"] == "stock"]
    out = []
    for _, opt in options.iterrows():
        want_side = "buy" if opt["opt_type"] == "P" else "sell"
        cand = stocks[
            (stocks["underlying"] == opt["underlying"])
            & (stocks["side"] == want_side)
            & ((stocks["price"] - opt["strike"]).abs() <= tol * max(opt["strike"], 1))
        ]
        for _, st in cand.iterrows():
            out.append({
                "option_code": opt["code"],
                "underlying": opt["underlying"],
                "opt_type": opt["opt_type"],
                "strike": opt["strike"],
                "stock_deal_id": st["deal_id"],
                "stock_side": st["side"],
                "stock_price": st["price"],
                "stock_time": st["trade_time"],
            })
    return pd.DataFrame(out, columns=cols)


def detect_rolls(deals: pd.DataFrame, window_days: int = 1) -> pd.DataFrame:
    """Heuristic roll detection: a buy-to-close paired with a same-day reopen further out.

    Within each ``(underlying, opt_type)`` group, each buy-to-close fill is matched
    to a sell-to-open fill whose ``trade_time`` is within ``window_days`` and whose
    ``expiry`` is later (rolling the position out in time). ``net_credit`` is the new
    credit minus the closing debit, so positive means you rolled for a net credit.
    """
    cols = ["underlying", "opt_type", "closed_code", "closed_expiry", "new_code",
            "new_expiry", "roll_time", "net_credit"]
    if deals.empty:
        return pd.DataFrame(columns=cols)

    options = deals[deals["sec_type"] == "option"].copy()
    if options.empty:
        return pd.DataFrame(columns=cols)

    window = pd.Timedelta(days=window_days)
    rows = []
    for (underlying, opt_type), grp in options.groupby(["underlying", "opt_type"]):
        closes = grp[grp["side"] == "buy"]
        opens = grp[grp["side"] == "sell"]
        if closes.empty or opens.empty:
            continue
        used_open_ids = set()
        for _, close in closes.iterrows():
            close_exp = close["expiry"]
            cand = opens[
                (opens["trade_time"] - close["trade_time"]).abs() <= window
            ]
            if pd.notna(close_exp):
                cand = cand[cand["expiry"] > close_exp]
            cand = cand[~cand["deal_id"].isin(used_open_ids)]
            if cand.empty:
                continue
            # Pair with the nearest reopen in time.
            new = cand.iloc[
                (cand["trade_time"] - close["trade_time"]).abs().argmin()]
            used_open_ids.add(new["deal_id"])
            rows.append({
                "underlying": underlying,
                "opt_type": opt_type,
                "closed_code": close["code"],
                "closed_expiry": close_exp,
                "new_code": new["code"],
                "new_expiry": new["expiry"],
                "roll_time": new["trade_time"],
                "net_credit": float(close["premium"]) + float(new["premium"]),
            })
    return pd.DataFrame(rows, columns=cols).sort_values(
        "roll_time").reset_index(drop=True) if rows else pd.DataFrame(columns=cols)


def build_cycles(deals: pd.DataFrame, as_of: date | None = None) -> pd.DataFrame:
    """Per-underlying wheel summary across all legs and stock trades.

    A pragmatic rollup (not a strict per-cycle split): for each underlying it
    sums option premium, stock realized P&L, fees, and net P&L, plus current
    share balance so you can see open vs. closed wheels at a glance.
    """
    cols = ["underlying", "option_premium", "stock_pnl", "fees", "net_pnl",
            "share_balance", "n_legs", "first_trade", "last_trade"]
    if deals.empty:
        return pd.DataFrame(columns=cols)

    rows = []
    for underlying, grp in deals.groupby("underlying"):
        opt = grp[grp["sec_type"] == "option"]
        stk = grp[grp["sec_type"] == "stock"]

        option_premium = opt["premium"].sum()
        fees = grp["fee"].fillna(0).sum()
        # Realized stock P&L (only shares sold/called away count).
        stock_pnl = _matched_stock_pnl(stk)
        share_balance = stk.apply(
            lambda r: r["qty"] * (1 if r["side"] == "buy" else -1), axis=1
        ).sum() if not stk.empty else 0.0

        rows.append({
            "underlying": underlying,
            "option_premium": option_premium,
            "stock_pnl": stock_pnl,
            "fees": fees,
            "net_pnl": option_premium + stock_pnl - fees,
            "share_balance": share_balance,
            "n_legs": opt["code"].nunique(),
            "first_trade": grp["trade_time"].min(),
            "last_trade": grp["trade_time"].max(),
        })
    return pd.DataFrame(rows, columns=cols).sort_values("net_pnl", ascending=False).reset_index(drop=True)
