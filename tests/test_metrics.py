from datetime import date

import pandas as pd

from src import metrics, wheel
from tests.test_wheel import make_deal


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
