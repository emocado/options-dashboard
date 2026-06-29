import math
from datetime import date

import pandas as pd

from src import metrics, wheel
from tests.test_wheel import make_deal


def make_leg(role, status, realized_pnl, premium, close_time, *, capital=10000.0,
             roc=0.02, annualized_roc=0.25, won=None):
    """Hand-built leg row matching build_option_legs' columns."""
    if won is None and not pd.isna(realized_pnl):
        won = 1.0 if realized_pnl > 0 else 0.0
    return {
        "underlying": "AAPL", "role": role, "opt_type": "P" if role == "CSP" else "C",
        "strike": 100.0, "expiry": pd.Timestamp(close_time or "2025-01-01"),
        "code": f"X{role}{close_time}", "contracts": 1.0,
        "open_time": pd.Timestamp(close_time or "2025-01-01") - pd.Timedelta(days=10),
        "close_time": pd.Timestamp(close_time) if close_time else pd.NaT,
        "premium_collected": premium, "fees": 0.0, "realized_pnl": realized_pnl,
        "status": status, "days_held": 10, "capital": capital, "roc": roc,
        "annualized_roc": annualized_roc, "won": won,
    }


def sample_legs():
    return pd.DataFrame([
        make_leg("CSP", "closed", 100.0, 100.0, "2025-06-20", capital=15000.0),
        make_leg("CC", "closed", -40.0, 60.0, "2025-06-25", capital=6000.0),
        make_leg("CSP", "expired", 50.0, 50.0, "2025-03-01", capital=5000.0),
        make_leg("CSP", "open", float("nan"), 30.0, None, won=float("nan")),
    ])


def _legs_across_two_months():
    """Two closed CSPs that expire in different months, plus one still open."""
    deals = pd.DataFrame([
        # June: sold + bought back for a +197 net
        make_deal("d1", "US.AAPL250620P00150000", "option", "P", 150.0,
                  "2025-06-20", "sell", 1, 2.50, premium=250.0, fee=1.5,
                  trade_time="2025-06-01"),
        make_deal("d2", "US.AAPL250620P00150000", "option", "P", 150.0,
                  "2025-06-20", "buy", 1, 0.50, premium=-50.0, fee=1.5,
                  trade_time="2025-06-10"),
        # July: expired worthless, keeps full 300 credit minus a fee
        make_deal("d3", "US.TSLA250718P00200000", "option", "P", 200.0,
                  "2025-07-18", "sell", 1, 3.00, premium=300.0, fee=1.5,
                  trade_time="2025-07-01", underlying="TSLA"),
        # Still open -> excluded from realized
        make_deal("d4", "US.NVDA251231P00100000", "option", "P", 100.0,
                  "2025-12-31", "sell", 1, 1.50, premium=150.0, fee=1.5,
                  trade_time="2025-07-05", underlying="NVDA"),
    ])
    return wheel.build_option_legs(deals, as_of=date(2025, 8, 1))


def test_monthly_realized_buckets_by_close_month():
    legs = _legs_across_two_months()
    mr = metrics.monthly_realized(legs)

    assert list(mr["month"]) == [pd.Timestamp("2025-06-01"), pd.Timestamp("2025-07-01")]
    assert mr.loc[mr["month"] == pd.Timestamp("2025-06-01"), "realized_pnl"].iloc[0] == 197.0
    assert mr.loc[mr["month"] == pd.Timestamp("2025-07-01"), "realized_pnl"].iloc[0] == 298.5
    # Open leg contributes nothing to realized.
    assert mr["realized_pnl"].sum() == 197.0 + 298.5


def test_monthly_realized_empty():
    empty = wheel.build_option_legs(pd.DataFrame())
    assert metrics.monthly_realized(empty).empty


def test_monthly_realized_total_matches_cumulative():
    legs = _legs_across_two_months()
    mr = metrics.monthly_realized(legs)
    cum = metrics.cumulative_realized(legs)
    assert round(mr["realized_pnl"].sum(), 6) == round(cum["cum_pnl"].iloc[-1], 6)


# ---------------------------------------------------------------------------
# period_start
# ---------------------------------------------------------------------------

def test_period_start_days_and_ytd():
    as_of = date(2025, 6, 30)
    assert metrics.period_start("14D", as_of) == pd.Timestamp("2025-06-16")
    assert metrics.period_start("90D", as_of) == pd.Timestamp("2025-04-01")
    assert metrics.period_start("YTD", as_of) == pd.Timestamp("2025-01-01")
    assert metrics.period_start("1Y", as_of) == pd.Timestamp("2024-06-30")
    assert metrics.period_start("ALL", as_of) is None


# ---------------------------------------------------------------------------
# performance_stats
# ---------------------------------------------------------------------------

def test_performance_stats_all_history():
    s = metrics.performance_stats(sample_legs(), since=None)
    assert s["n_trades"] == 3            # the open leg is excluded
    assert s["n_wins"] == 2 and s["n_losses"] == 1
    assert s["total_pnl"] == 110.0
    assert s["total_premium"] == 210.0
    assert math.isclose(s["win_rate"], 2 / 3)
    assert s["avg_win"] == 75.0
    assert s["avg_loss"] == -40.0
    assert math.isclose(s["profit_factor"], 150.0 / 40.0)
    assert math.isclose(s["expectancy"], 110.0 / 3)
    assert s["best_trade"] == 100.0 and s["worst_trade"] == -40.0


def test_performance_stats_windowed():
    since = metrics.period_start("30D", date(2025, 6, 30))
    s = metrics.performance_stats(sample_legs(), since=since)
    assert s["n_trades"] == 2            # March leg drops out of a 30-day window
    assert s["total_pnl"] == 60.0


def test_performance_stats_empty():
    s = metrics.performance_stats(pd.DataFrame(), since=None)
    assert s["n_trades"] == 0
    assert math.isnan(s["win_rate"])


# ---------------------------------------------------------------------------
# by_strategy
# ---------------------------------------------------------------------------

def test_by_strategy_groups_by_role():
    bs = metrics.by_strategy(sample_legs())
    by = {r["strategy"]: r for _, r in bs.iterrows()}
    assert by["Cash-Secured Put"]["n_trades"] == 2
    assert by["Cash-Secured Put"]["win_rate"] == 1.0
    assert by["Cash-Secured Put"]["avg_premium"] == 75.0
    assert by["Cash-Secured Put"]["total_realized_pnl"] == 150.0
    assert by["Covered Call"]["n_trades"] == 1
    assert by["Covered Call"]["total_realized_pnl"] == -40.0


# ---------------------------------------------------------------------------
# monthly_yield
# ---------------------------------------------------------------------------

def test_monthly_yield():
    my = metrics.monthly_yield(sample_legs()).set_index("month")
    june = my.loc[pd.Timestamp("2025-06-01")]
    assert june["realized_pnl"] == 60.0
    assert june["capital"] == 21000.0
    assert math.isclose(june["yield_pct"], 60.0 / 21000.0)
    assert math.isclose(june["annualized_pct"], 60.0 / 21000.0 * 12)
    march = my.loc[pd.Timestamp("2025-03-01")]
    assert math.isclose(march["yield_pct"], 0.01)
