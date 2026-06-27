"""Portfolio-level metrics derived from deals, legs, account, and positions.

Pure functions over pandas DataFrames so they can be tested without a DB or
moomoo connection.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from . import wheel


def premium_inflows(deals: pd.DataFrame) -> pd.DataFrame:
    """Sell-to-open option credits over time: [trade_time, underlying, credit]."""
    if deals.empty:
        return pd.DataFrame(columns=["trade_time", "underlying", "credit"])
    opens = deals[(deals["sec_type"] == "option") & (deals["side"] == "sell")].copy()
    opens["credit"] = opens["premium"]
    return opens[["trade_time", "underlying", "credit"]].dropna(subset=["trade_time"])


def kpis(deals: pd.DataFrame, legs: pd.DataFrame,
         account: dict | None, positions: pd.DataFrame | None,
         as_of: date | None = None) -> dict:
    """Headline numbers for the Overview KPI cards."""
    today = pd.Timestamp(as_of or date.today())
    inflows = premium_inflows(deals)

    def period_sum(since: pd.Timestamp) -> float:
        if inflows.empty:
            return 0.0
        return float(inflows.loc[inflows["trade_time"] >= since, "credit"].sum())

    week_start = today - pd.Timedelta(days=today.dayofweek)
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)

    closed = legs[legs["status"].isin(["closed", "expired", "assigned"])] if not legs.empty else legs
    open_legs = legs[legs["status"] == "open"] if not legs.empty else legs

    total_premium = float(inflows["credit"].sum()) if not inflows.empty else 0.0
    realized_option_pnl = float(closed["realized_pnl"].sum()) if not closed.empty else 0.0

    # Realized stock P&L from called-away shares (held shares are NOT a loss).
    stock_pnl = wheel.realized_stock_pnl(deals)

    net_realized = realized_option_pnl + stock_pnl

    # Only cash-secured puts tie up cash; covered calls are backed by shares
    # already counted in the account's net asset, so they're excluded here.
    open_csp = open_legs[open_legs["role"] == "CSP"] if not open_legs.empty else open_legs
    capital_secured = float(open_csp["capital"].sum()) if not open_csp.empty else 0.0
    net_asset = float(account["net_asset"]) if account else float("nan")
    pct_deployed = (capital_secured / net_asset) if account and net_asset else float("nan")

    unrealized = 0.0
    if positions is not None and not positions.empty and "pl_val" in positions:
        unrealized = float(pd.to_numeric(positions["pl_val"], errors="coerce").sum())

    if not closed.empty and closed["won"].notna().any():
        win_rate = float(closed["won"].mean())
    else:
        win_rate = float("nan")

    # Capital-weighted annualized ROC across closed legs.
    cw = closed.dropna(subset=["annualized_roc"]) if not closed.empty else closed
    if not cw.empty and cw["capital"].sum() > 0:
        ann_roc = float((cw["annualized_roc"] * cw["capital"]).sum() / cw["capital"].sum())
    else:
        ann_roc = float("nan")

    return {
        "total_premium": total_premium,
        "net_realized_pnl": net_realized,
        "premium_week": period_sum(week_start),
        "premium_month": period_sum(month_start),
        "premium_ytd": period_sum(year_start),
        "capital_secured": capital_secured,
        "pct_deployed": pct_deployed,
        "unrealized_pnl": unrealized,
        "win_rate": win_rate,
        "annualized_roc": ann_roc,
        "open_legs": int(len(open_legs)) if not open_legs.empty else 0,
        "net_asset": net_asset,
        "cash": float(account["cash"]) if account else float("nan"),
        "power": float(account["power"]) if account else float("nan"),
    }


def monthly_premium(deals: pd.DataFrame) -> pd.DataFrame:
    """Premium credits grouped by calendar month: [month, credit]."""
    inflows = premium_inflows(deals)
    if inflows.empty:
        return pd.DataFrame(columns=["month", "credit"])
    inflows = inflows.copy()
    inflows["month"] = inflows["trade_time"].dt.to_period("M").dt.to_timestamp()
    out = inflows.groupby("month", as_index=False)["credit"].sum()
    return out.sort_values("month")


def monthly_realized(legs: pd.DataFrame) -> pd.DataFrame:
    """Realized option P&L grouped by close month: [month, realized_pnl].

    Same basis as :func:`cumulative_realized` (closed/expired/assigned option
    legs, keyed on close date) but bucketed per calendar month instead of
    accumulated, so you can see which months actually made money.
    """
    if legs.empty:
        return pd.DataFrame(columns=["month", "realized_pnl"])
    closed = legs[legs["status"].isin(["closed", "expired", "assigned"])].copy()
    closed = closed.dropna(subset=["close_time"])
    if closed.empty:
        return pd.DataFrame(columns=["month", "realized_pnl"])
    closed["month"] = closed["close_time"].dt.to_period("M").dt.to_timestamp()
    out = closed.groupby("month", as_index=False)["realized_pnl"].sum()
    return out.sort_values("month")


def cumulative_realized(legs: pd.DataFrame) -> pd.DataFrame:
    """Cumulative realized option P&L over close dates: [date, cum_pnl]."""
    if legs.empty:
        return pd.DataFrame(columns=["date", "cum_pnl"])
    closed = legs[legs["status"].isin(["closed", "expired", "assigned"])].copy()
    closed = closed.dropna(subset=["close_time"])
    if closed.empty:
        return pd.DataFrame(columns=["date", "cum_pnl"])
    closed = closed.sort_values("close_time")
    closed["cum_pnl"] = closed["realized_pnl"].cumsum()
    return closed.rename(columns={"close_time": "date"})[["date", "cum_pnl"]]


def by_ticker(deals: pd.DataFrame, legs: pd.DataFrame) -> pd.DataFrame:
    """Per-underlying summary: premium, realized P&L, open capital, win rate."""
    if legs.empty:
        return pd.DataFrame(
            columns=["underlying", "premium", "realized_pnl", "open_capital",
                     "win_rate", "n_legs"]
        )
    rows = []
    for underlying, grp in legs.groupby("underlying"):
        closed = grp[grp["status"].isin(["closed", "expired", "assigned"])]
        open_legs = grp[grp["status"] == "open"]
        win_rate = float(closed["won"].mean()) if not closed.empty and closed["won"].notna().any() else float("nan")
        rows.append({
            "underlying": underlying,
            "premium": float(grp["premium_collected"].sum()),
            "realized_pnl": float(closed["realized_pnl"].sum()) if not closed.empty else 0.0,
            "open_capital": float(open_legs["capital"].sum()) if not open_legs.empty else 0.0,
            "win_rate": win_rate,
            "n_legs": int(len(grp)),
        })
    return pd.DataFrame(rows).sort_values("realized_pnl", ascending=False).reset_index(drop=True)
