from datetime import date

import pandas as pd

from src import wheel


def make_deal(deal_id, code, sec_type, opt_type, strike, expiry, side, qty,
              price, premium, fee=0.0, trade_time="2025-06-01",
              underlying="AAPL", wheel_event=None):
    return {
        "deal_id": deal_id,
        "code": code,
        "sec_type": sec_type,
        "opt_type": opt_type,
        "strike": strike,
        "expiry": pd.Timestamp(expiry) if expiry else pd.NaT,
        "side": side,
        "qty": qty,
        "price": price,
        "premium": premium,
        "fee": fee,
        "trade_time": pd.Timestamp(trade_time),
        "underlying": underlying,
        "wheel_event": wheel_event,
    }


def test_gross_premium_sign():
    assert wheel.gross_premium("sell", 1, 2.5) == 250.0
    assert wheel.gross_premium("buy", 1, 2.5) == -250.0
    assert wheel.gross_premium("sell", 3, 1.0) == 300.0


def test_classify_event():
    assert wheel.classify_event("sell", "option", "P") == wheel.SELL_PUT
    assert wheel.classify_event("sell", "option", "C") == wheel.SELL_CALL
    assert wheel.classify_event("buy", "option", "P") == wheel.BUY_TO_CLOSE
    assert wheel.classify_event("buy", "stock", None) == wheel.STOCK_BUY
    assert wheel.classify_event("sell", "stock", None) == wheel.STOCK_SELL


def test_closed_leg_pnl():
    deals = pd.DataFrame([
        make_deal("d1", "US.AAPL250620P00150000", "option", "P", 150.0,
                  "2025-06-20", "sell", 1, 2.50, premium=250.0, fee=1.5,
                  trade_time="2025-06-01"),
        make_deal("d2", "US.AAPL250620P00150000", "option", "P", 150.0,
                  "2025-06-20", "buy", 1, 0.50, premium=-50.0, fee=1.5,
                  trade_time="2025-06-10"),
    ])
    legs = wheel.build_option_legs(deals, as_of=date(2025, 7, 1))
    assert len(legs) == 1
    leg = legs.iloc[0]
    assert leg["status"] == "closed"
    assert leg["contracts"] == 1
    assert leg["capital"] == 150.0 * 100
    assert leg["realized_pnl"] == 250.0 - 50.0 - 3.0  # 197
    assert leg["role"] == "CSP"
    assert bool(leg["won"]) is True


def test_expired_leg_keeps_full_credit():
    deals = pd.DataFrame([
        make_deal("d1", "US.TSLA250615P00200000", "option", "P", 200.0,
                  "2025-06-15", "sell", 1, 3.00, premium=300.0, fee=1.5,
                  trade_time="2025-06-01", underlying="TSLA"),
    ])
    legs = wheel.build_option_legs(deals, as_of=date(2025, 7, 1))
    leg = legs.iloc[0]
    assert leg["status"] == "expired"
    assert leg["realized_pnl"] == 300.0 - 1.5
    assert bool(leg["won"]) is True


def test_open_leg_not_realized():
    deals = pd.DataFrame([
        make_deal("d1", "US.NVDA251231P00100000", "option", "P", 100.0,
                  "2025-12-31", "sell", 1, 1.50, premium=150.0, fee=1.5,
                  trade_time="2025-06-01", underlying="NVDA"),
    ])
    legs = wheel.build_option_legs(deals, as_of=date(2025, 7, 1))
    leg = legs.iloc[0]
    assert leg["status"] == "open"
    assert pd.isna(leg["realized_pnl"])


def test_detect_rolls_pairs_close_with_later_reopen():
    deals = pd.DataFrame([
        # Buy-to-close the near-dated put for a small debit...
        make_deal("c1", "US.AAPL250620P00150000", "option", "P", 150.0,
                  "2025-06-20", "buy", 1, 0.50, premium=-50.0,
                  trade_time="2025-06-10"),
        # ...and reopen the same day further out for a credit -> a roll.
        make_deal("o1", "US.AAPL250718P00150000", "option", "P", 150.0,
                  "2025-07-18", "sell", 1, 2.00, premium=200.0,
                  trade_time="2025-06-10"),
    ])
    rolls = wheel.detect_rolls(deals)
    assert len(rolls) == 1
    row = rolls.iloc[0]
    assert row["underlying"] == "AAPL"
    assert row["closed_expiry"] == pd.Timestamp("2025-06-20")
    assert row["new_expiry"] == pd.Timestamp("2025-07-18")
    assert row["net_credit"] == 150.0  # -50 debit + 200 credit


def test_detect_rolls_ignores_distant_reopen():
    deals = pd.DataFrame([
        make_deal("c1", "US.AAPL250620P00150000", "option", "P", 150.0,
                  "2025-06-20", "buy", 1, 0.50, premium=-50.0,
                  trade_time="2025-06-10"),
        # Reopen a week later -> outside the 1-day window, not a roll.
        make_deal("o1", "US.AAPL250718P00150000", "option", "P", 150.0,
                  "2025-07-18", "sell", 1, 2.00, premium=200.0,
                  trade_time="2025-06-17"),
    ])
    assert wheel.detect_rolls(deals).empty


def test_detect_rolls_empty():
    assert wheel.detect_rolls(pd.DataFrame()).empty


def test_cycle_rollup_full_wheel():
    deals = pd.DataFrame([
        # Sold put, collected 250
        make_deal("o1", "US.AAPL250620P00150000", "option", "P", 150.0,
                  "2025-06-20", "sell", 1, 2.50, premium=250.0,
                  trade_time="2025-06-01"),
        # Assigned: bought 100 shares @150
        make_deal("s1", "US.AAPL", "stock", None, None, None, "buy", 100,
                  150.0, premium=0.0, trade_time="2025-06-20"),
        # Sold covered call, collected 120
        make_deal("o2", "US.AAPL250718C00155000", "option", "C", 155.0,
                  "2025-07-18", "sell", 1, 1.20, premium=120.0,
                  trade_time="2025-06-25"),
        # Called away: sold 100 shares @155
        make_deal("s2", "US.AAPL", "stock", None, None, None, "sell", 100,
                  155.0, premium=0.0, trade_time="2025-07-18"),
    ])
    cycles = wheel.build_cycles(deals, as_of=date(2025, 8, 1))
    assert len(cycles) == 1
    row = cycles.iloc[0]
    assert row["underlying"] == "AAPL"
    assert row["option_premium"] == 370.0
    assert row["stock_pnl"] == 500.0       # 15500 - 15000
    assert row["net_pnl"] == 870.0
    assert row["share_balance"] == 0.0
    assert row["n_legs"] == 2
