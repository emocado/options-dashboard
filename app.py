"""Options Wheel Dashboard — Streamlit entry point.

Run with:  python -m streamlit run app.py
Works fully offline with manual entry; click "Sync from moomoo" to pull data
from a running moomoo OpenD gateway.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from src import db, metrics, wheel, auth
from src.config import (
    Config, MoomooConfig, FeesConfig, AppConfig,
    load_config, save_config, SECURITY_FIRMS, TRD_MARKETS, TRD_ENVS,
)
from src.options import build_option_code

st.set_page_config(page_title="Options Wheel Dashboard", page_icon="🛞", layout="wide")


def _promote_secrets_to_env() -> None:
    """On Streamlit Community Cloud, config lives in ``st.secrets`` (not env vars).

    Promote the keys the Streamlit-free layers read from ``os.environ`` (the Turso
    connection and the cloud-mode flag) so ``db`` can pick up the remote backend.
    """
    for key in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN", "DASHBOARD_MODE"):
        try:
            value = st.secrets.get(key)
        except Exception:
            value = None
        if value:
            os.environ.setdefault(key, str(value))


_promote_secrets_to_env()
# Cloud mode hides moomoo-only controls (Sync/Settings/Test) — OpenD is unreachable
# from the cloud; data arrives via the local sync agent writing to Turso.
CLOUD = os.environ.get("DASHBOARD_MODE") == "cloud"

db.init_db()
CFG = load_config()

# Currency the stored figures are denominated in (US-market wheels => USD).
BASE_CUR = CFG.app.currency
# What the user is currently viewing in, plus the base->display factor. These
# are reset each run by the sidebar toggle (session-only, never saved to config).
DISPLAY_CUR = BASE_CUR
FX_FACTOR = 1.0


@st.cache_data(ttl=3600, show_spinner=False)
def _fx_rate(base: str, quote: str) -> tuple[float, bool]:
    """Cached live FX lookup (1h TTL). Returns (rate, is_live)."""
    from src import fx
    return fx.fetch_rate(base, quote)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def money(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return f"{DISPLAY_CUR} {x * FX_FACTOR:,.2f}"


def to_display_currency(df: pd.DataFrame, cols) -> pd.DataFrame:
    """Scale the given money columns of ``df`` into the display currency.

    A no-op when viewing in the base currency. Leaves market quotes (strike,
    per-share price) untouched — only aggregate cash amounts are converted.
    """
    if df is None or df.empty or FX_FACTOR == 1.0:
        return df
    out = df.copy()
    for c in cols:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce") * FX_FACTOR
    return out


def _table_currency_note(base_note: str = "") -> str:
    """Append a conversion note to a table caption when not in the base currency."""
    if FX_FACTOR == 1.0:
        return base_note
    note = (f"💱 Cash columns in {DISPLAY_CUR} at {FX_FACTOR:,.4f}; "
            f"strikes/prices stay in {BASE_CUR}.")
    return f"{base_note} {note}".strip()


def pct(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return f"{x * 100:,.1f}%"


def ratio(x) -> str:
    """Plain ratio with two decimals (e.g. profit factor); em-dash when missing."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return f"{x:,.2f}"


# ---------------------------------------------------------------------------
# Sidebar: sync, settings, filters
# ---------------------------------------------------------------------------

def run_sync() -> None:
    from src import moomoo_client, sync_service
    try:
        messages = sync_service.sync_and_persist(CFG)
    except moomoo_client.MoomooUnavailable as exc:
        st.sidebar.error(str(exc))
        return
    except moomoo_client.OpenDUnreachable as exc:
        st.sidebar.warning(str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        st.sidebar.error(f"Sync failed: {exc}")
        return

    for msg in messages:
        st.sidebar.write("• " + msg)
    st.sidebar.success("Sync complete.")


def currency_toggle() -> None:
    """Sidebar switch between the base currency and SGD (session-only).

    Sets the module-level display currency / FX factor used by ``money()`` and
    the chart/table converters. The choice is not persisted to config, so a
    fresh load always starts in the base currency.
    """
    global DISPLAY_CUR, FX_FACTOR
    alt = "SGD" if BASE_CUR != "SGD" else "USD"
    choice = st.sidebar.radio(
        "💱 Display currency", [BASE_CUR, alt], horizontal=True,
        help=f"Figures are stored in {BASE_CUR}; switching converts them at the "
             "live rate.",
    )
    rate, is_live = _fx_rate(BASE_CUR, choice)
    DISPLAY_CUR, FX_FACTOR = choice, rate
    if choice != BASE_CUR:
        tag = "live" if is_live else "approx — rate fetch failed"
        st.sidebar.caption(f"{BASE_CUR}→{choice} {rate:,.4f} ({tag})")


def sidebar() -> list[str]:
    st.sidebar.title("🛞 Wheel Dashboard")
    if CLOUD:
        snap = db.latest_account_snapshot()
        ts = snap.get("ts") if snap else None
        st.sidebar.caption(
            f"📅 Last synced: {ts.replace('T', ' ')}" if ts
            else "No sync yet — run the sync agent on your PC."
        )
    else:
        st.sidebar.caption(f"Account env: **{CFG.moomoo.trd_env}** · {CFG.moomoo.security_firm}")

    who = auth.current_email()
    if who:
        st.sidebar.caption(f"👤 {who}")
    if st.sidebar.button("Log out", use_container_width=True):
        auth.logout()

    currency_toggle()

    if not CLOUD:
        if st.sidebar.button("🔄 Sync from moomoo", use_container_width=True):
            with st.spinner("Talking to moomoo OpenD…"):
                run_sync()

        with st.sidebar.expander("⚙️ Settings"):
            with st.form("settings_form"):
                host = st.text_input("OpenD host", CFG.moomoo.host)
                port = st.number_input("OpenD port", value=CFG.moomoo.port, step=1)
                firm = st.selectbox("Security firm", SECURITY_FIRMS,
                                    index=SECURITY_FIRMS.index(CFG.moomoo.security_firm)
                                    if CFG.moomoo.security_firm in SECURITY_FIRMS else 0)
                market = st.selectbox("Trade market", TRD_MARKETS,
                                      index=TRD_MARKETS.index(CFG.moomoo.trd_market)
                                      if CFG.moomoo.trd_market in TRD_MARKETS else 0)
                env = st.selectbox("Trade env", TRD_ENVS,
                                   index=TRD_ENVS.index(CFG.moomoo.trd_env)
                                   if CFG.moomoo.trd_env in TRD_ENVS else 0)
                hist = st.number_input("History days to sync", value=CFG.moomoo.history_days, step=30)
                fee = st.number_input("Fee per contract", value=float(CFG.fees.per_contract), step=0.25)
                if st.form_submit_button("Save settings"):
                    save_config(Config(
                        moomoo=MoomooConfig(host=host, port=int(port), security_firm=firm,
                                            trd_market=market, trd_env=env, history_days=int(hist)),
                        fees=FeesConfig(per_contract=float(fee)),
                        app=AppConfig(currency=BASE_CUR),
                    ))
                    st.success("Saved. Rerun to apply.")
                    st.rerun()

        with st.sidebar.expander("🔌 Test connection"):
            if st.button("Ping OpenD"):
                from src import moomoo_client
                ok, msg = moomoo_client.test_connection(CFG)
                (st.success if ok else st.error)(msg)

    deals = db.get_deals()
    tickers = sorted(deals["underlying"].dropna().unique()) if not deals.empty else []
    selected = st.sidebar.multiselect("Filter tickers", tickers, default=tickers)
    return selected


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

def tab_overview(deals, legs, account, positions):
    k = metrics.kpis(deals, legs, account, positions)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total premium collected", money(k["total_premium"]))
    c2.metric("Net realized P&L", money(k["net_realized_pnl"]))
    c3.metric("Unrealized P&L (open)", money(k["unrealized_pnl"]))
    c4.metric("Win rate", pct(k["win_rate"]))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Premium this week", money(k["premium_week"]))
    c2.metric("Premium this month", money(k["premium_month"]))
    c3.metric("Premium YTD", money(k["premium_ytd"]))
    c4.metric("Annualized ROC", pct(k["annualized_roc"]))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Capital secured (open)", money(k["capital_secured"]))
    c2.metric("% of account deployed", pct(k["pct_deployed"]))
    c3.metric("Account net asset", money(k["net_asset"]))
    c4.metric("Cash / buying power", f"{money(k['cash'])} / {money(k['power'])}")

    st.divider()
    cur = DISPLAY_CUR
    left, right = st.columns(2)

    with left:
        st.subheader("Cumulative realized P&L")
        cum = to_display_currency(metrics.cumulative_realized(legs), ["cum_pnl"])
        if cum.empty:
            st.info("No closed trades yet.")
        else:
            st.plotly_chart(
                px.line(cum, x="date", y="cum_pnl", markers=True,
                        labels={"cum_pnl": f"Cumulative P&L ({cur})", "date": "Date"}),
                use_container_width=True)

    with right:
        st.subheader("Monthly realized P&L")
        mr = to_display_currency(metrics.monthly_realized(legs), ["realized_pnl"])
        if mr.empty:
            st.info("No closed trades yet.")
        else:
            fig = px.bar(mr, x="month", y="realized_pnl",
                         labels={"realized_pnl": f"Realized P&L ({cur})", "month": "Month"})
            fig.update_traces(marker_color=[
                "#2ca02c" if v >= 0 else "#d62728" for v in mr["realized_pnl"]])
            st.plotly_chart(fig, use_container_width=True)

    left, right = st.columns(2)
    with left:
        st.subheader("Monthly premium income")
        mp = to_display_currency(metrics.monthly_premium(deals), ["credit"])
        if mp.empty:
            st.info("No premium collected yet.")
        else:
            st.plotly_chart(
                px.bar(mp, x="month", y="credit",
                       labels={"credit": f"Premium ({cur})", "month": "Month"}),
                use_container_width=True)

    bt = metrics.by_ticker(deals, legs)
    with right:
        st.subheader("Realized P&L by ticker")
        if bt.empty:
            st.info("No data.")
        else:
            st.plotly_chart(
                px.bar(to_display_currency(bt, ["realized_pnl"]),
                       x="underlying", y="realized_pnl",
                       labels={"realized_pnl": f"Realized P&L ({cur})",
                               "underlying": "Ticker"}),
                use_container_width=True)

    left, _ = st.columns(2)
    with left:
        st.subheader("Open capital by ticker")
        open_cap = to_display_currency(bt[bt["open_capital"] > 0], ["open_capital"])
        if open_cap.empty:
            st.info("No open positions.")
        else:
            st.plotly_chart(px.pie(open_cap, names="underlying", values="open_capital"),
                            use_container_width=True)


def tab_performance(legs):
    st.subheader("Performance")
    st.caption("Win/loss analytics over a chosen window (by trade close date).")

    period = st.radio("Timeframe", metrics.PERIOD_LABELS,
                      index=len(metrics.PERIOD_LABELS) - 1, horizontal=True)
    since = metrics.period_start(period)
    s = metrics.performance_stats(legs, since)

    if s["n_trades"] == 0:
        st.info("No closed trades in this window yet.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total P/L", money(s["total_pnl"]))
    c2.metric("Win rate", pct(s["win_rate"]))
    c3.metric("Trades", s["n_trades"])
    c4.metric("Profit factor", ratio(s["profit_factor"]))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Avg win", money(s["avg_win"]))
    c2.metric("Avg loss", money(s["avg_loss"]))
    c3.metric("Expectancy / trade", money(s["expectancy"]))
    c4.metric("Premium collected", money(s["total_premium"]))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Best trade", money(s["best_trade"]))
    c2.metric("Worst trade", money(s["worst_trade"]))
    c3.metric("Wins", s["n_wins"])
    c4.metric("Losses", s["n_losses"])

    st.divider()
    cur = DISPLAY_CUR
    left, right = st.columns(2)
    with left:
        st.subheader("Equity curve (realized)")
        cum = metrics.cumulative_realized(legs)
        if since is not None and not cum.empty:
            cum = cum[cum["date"] >= since]
        cum = to_display_currency(cum, ["cum_pnl"])
        if cum.empty:
            st.info("No closed trades in this window.")
        else:
            st.plotly_chart(
                px.line(cum, x="date", y="cum_pnl", markers=True,
                        labels={"cum_pnl": f"Cumulative P&L ({cur})", "date": "Date"}),
                use_container_width=True, key="perf_equity")

    with right:
        st.subheader("Monthly yield on capital")
        my = metrics.monthly_yield(legs)
        if my.empty:
            st.info("No closed trades yet.")
        else:
            my = my.copy()
            my["yield_%"] = my["yield_pct"] * 100
            fig = px.bar(my, x="month", y="yield_%",
                         labels={"yield_%": "Monthly yield (%)", "month": "Month"},
                         hover_data={"annualized_pct": ":.1%"})
            fig.update_traces(marker_color=[
                "#2ca02c" if v >= 0 else "#d62728" for v in my["yield_%"]])
            st.plotly_chart(fig, use_container_width=True, key="perf_yield")
            st.caption("Yield = realized P&L ÷ capital deployed for legs closed that "
                       "month; hover for the annualized projection.")


def tab_strategy(deals, legs):
    st.subheader("Strategy analytics")
    st.caption("Closed-trade performance broken down by strategy type.")

    bs = metrics.by_strategy(legs)
    if bs.empty:
        st.info("No closed trades yet.")
        return

    cycles = wheel.build_cycles(deals)
    completed = int((cycles["share_balance"] == 0).sum()) if not cycles.empty else 0
    c1, c2, c3 = st.columns(3)
    c1.metric("Strategy types traded", int(len(bs)))
    c2.metric("Closed legs", int(bs["n_trades"].sum()))
    c3.metric("Completed wheels", completed)

    disp = to_display_currency(bs, ["avg_premium", "total_realized_pnl"]).copy()
    disp["win_rate"] = (disp["win_rate"] * 100).round(1)
    disp["avg_roc"] = (disp["avg_roc"] * 100).round(2)
    disp["avg_annualized_roc"] = (disp["avg_annualized_roc"] * 100).round(1)
    disp = disp.rename(columns={
        "n_trades": "trades", "win_rate": "win %", "avg_premium": "avg premium",
        "avg_roc": "avg ROC %", "avg_annualized_roc": "avg ann. ROC %",
        "total_realized_pnl": "realized P&L"})
    st.dataframe(disp, use_container_width=True, hide_index=True)
    st.caption(_table_currency_note("win %, ROC % and ann. ROC % are percentages."))

    cur = DISPLAY_CUR
    left, right = st.columns(2)
    with left:
        st.subheader("Realized P&L by strategy")
        st.plotly_chart(
            px.bar(to_display_currency(bs, ["total_realized_pnl"]),
                   x="strategy", y="total_realized_pnl",
                   labels={"total_realized_pnl": f"Realized P&L ({cur})",
                           "strategy": "Strategy"}),
            use_container_width=True, key="strat_pnl")
    with right:
        st.subheader("Win rate by strategy")
        wr = bs.copy()
        wr["win_%"] = wr["win_rate"] * 100
        st.plotly_chart(
            px.bar(wr, x="strategy", y="win_%",
                   labels={"win_%": "Win rate (%)", "strategy": "Strategy"}),
            use_container_width=True, key="strat_winrate")

    st.divider()
    st.subheader("Detected rolls")
    rolls = wheel.detect_rolls(deals)
    if rolls.empty:
        st.info("No rolls detected (a buy-to-close paired with a later-dated reopen).")
    else:
        rv = to_display_currency(rolls, ["net_credit"]).rename(
            columns={"net_credit": "net credit"})
        st.dataframe(rv, use_container_width=True, hide_index=True)
        st.caption(_table_currency_note(
            "A roll = closing a short leg and reopening further out the same day."))


# Execution-quality scale used by the trade journal (1 worst .. 5 best).
RATING_LABELS = {1: "1 · Sloppy", 2: "2 · Rushed", 3: "3 · Okay",
                 4: "4 · Solid", 5: "5 · Textbook"}


def tab_journal(legs):
    st.subheader("Trade journal")
    st.caption("Capture your reasoning when you open a trade; rate your execution "
               "when you close it — *before* the P&L is revealed.")

    if legs.empty:
        st.info("No trades yet.")
        return

    journal = db.get_journal()
    rated_codes = set()
    if not journal.empty and "execution_rating" in journal:
        rated_codes = set(
            journal.loc[journal["execution_rating"].notna(), "code"].tolist())

    def leg_label(row) -> str:
        exp = row["expiry"].date() if pd.notna(row["expiry"]) else "—"
        return f"{row['underlying']} {row['role']} {row['strike']:g} exp {exp}"

    # 1) Log a thesis on an open leg.
    st.markdown("#### 1 · Log a thesis (open positions)")
    open_legs = legs[legs["status"] == "open"]
    if open_legs.empty:
        st.info("No open legs to journal.")
    else:
        labels = {leg_label(r): r["code"] for _, r in open_legs.iterrows()}
        with st.form("journal_thesis"):
            pick = st.selectbox("Open leg", list(labels.keys()))
            code = labels[pick]
            existing = db.get_journal_entry(code) or {}
            thesis = st.text_area("Thesis / reasoning",
                                  value=existing.get("thesis") or "",
                                  placeholder="Why this strike/expiry? IV, support, "
                                              "earnings, target outcome…")
            if st.form_submit_button("Save thesis"):
                db.upsert_journal(code, thesis=thesis)
                st.success("Thesis saved.")

    # Reveal the P&L of the leg we just rated (set on the previous run).
    revealed = st.session_state.pop("journal_revealed", None)
    if revealed:
        st.success(f"Logged your rating. Realized P&L was {money(revealed)}.")

    # 2) Rate execution on a closed leg before seeing the P&L.
    st.markdown("#### 2 · Close-out review (rate before P&L)")
    closed = legs[legs["status"].isin(["closed", "expired", "assigned"])]
    unrated = closed[~closed["code"].isin(rated_codes)] if not closed.empty else closed
    if unrated.empty:
        st.info("No closed trades awaiting a review.")
    else:
        labels = {leg_label(r): r["code"] for _, r in unrated.iterrows()}
        with st.form("journal_review"):
            pick = st.selectbox("Closed leg", list(labels.keys()))
            code = labels[pick]
            row = unrated[unrated["code"] == code].iloc[0]
            st.caption(f"Held {int(row['days_held'])} days · "
                       f"premium {money(row['premium_collected'])} · P&L hidden until rated")
            rating = st.radio("How well did you execute?",
                              list(RATING_LABELS.keys()),
                              format_func=lambda x: RATING_LABELS[x], horizontal=True)
            note = st.text_input("Review note (optional)", "")
            if st.form_submit_button("Submit rating & reveal P&L"):
                db.upsert_journal(code, execution_rating=int(rating), review_note=note)
                st.session_state["journal_revealed"] = float(row["realized_pnl"])
                st.rerun()

    # 3) Behavioral analytics: does execution quality track outcomes?
    st.markdown("#### 3 · Behavioral analytics")
    if journal.empty or journal["execution_rating"].notna().sum() == 0:
        st.info("Rate a few closed trades to see how execution quality tracks P&L.")
        return
    rated = journal[journal["execution_rating"].notna()].copy()
    # Cast to plain int so values match the int keys in RATING_LABELS on .map().
    rated["execution_rating"] = pd.to_numeric(
        rated["execution_rating"], errors="coerce").astype(int)
    merged = rated.merge(
        closed[["code", "underlying", "realized_pnl", "status"]], on="code", how="inner")
    if merged.empty:
        st.info("No rated trades matched to closed legs yet.")
        return

    by_rating = merged.groupby("execution_rating", as_index=False)["realized_pnl"].mean()
    by_rating["rating"] = by_rating["execution_rating"].map(RATING_LABELS)
    st.plotly_chart(
        px.bar(to_display_currency(by_rating, ["realized_pnl"]),
               x="rating", y="realized_pnl",
               labels={"realized_pnl": f"Avg realized P&L ({DISPLAY_CUR})",
                       "rating": "Execution rating"}),
        use_container_width=True, key="journal_rating_pnl")

    tbl = merged[["underlying", "execution_rating", "realized_pnl", "thesis",
                  "review_note", "status"]].copy()
    tbl["execution_rating"] = tbl["execution_rating"].map(RATING_LABELS)
    tbl = to_display_currency(tbl, ["realized_pnl"]).rename(
        columns={"execution_rating": "rating", "realized_pnl": "realized P&L"})
    st.dataframe(tbl, use_container_width=True, hide_index=True)
    st.caption(_table_currency_note("Thesis vs. outcome for every rated trade."))


def tab_open_positions(legs, positions):
    st.subheader("Open option positions")
    open_legs = legs[legs["status"] == "open"].copy() if not legs.empty else legs
    if open_legs.empty:
        st.info("No open option legs. Sell a CSP/CC or click Sync.")
        return

    open_legs["DTE"] = (open_legs["expiry"] - pd.Timestamp(date.today())).dt.days
    # Bring in live unrealized P&L from the positions snapshot, by code.
    if positions is not None and not positions.empty:
        open_legs = open_legs.merge(
            positions[["code", "pl_val", "market_val"]], on="code", how="left")
    else:
        open_legs["pl_val"] = float("nan")
        open_legs["market_val"] = float("nan")

    view = open_legs[["underlying", "role", "strike", "expiry", "DTE", "contracts",
                      "premium_collected", "capital", "market_val", "pl_val",
                      "annualized_roc"]].rename(columns={
        "premium_collected": "premium", "market_val": "current value",
        "pl_val": "unrealized P&L", "annualized_roc": "ann. ROC"})
    view = to_display_currency(
        view, ["premium", "capital", "current value", "unrealized P&L"])
    st.dataframe(view, use_container_width=True, hide_index=True)
    st.caption(_table_currency_note(
        "Unrealized P&L / current value come straight from moomoo's last sync."))


def tab_history(legs):
    st.subheader("Closed legs")
    closed = legs[legs["status"].isin(["closed", "expired", "assigned"])].copy() if not legs.empty else legs
    if closed.empty:
        st.info("No closed trades yet.")
        return
    view = closed[["underlying", "role", "strike", "expiry", "contracts",
                   "open_time", "close_time", "days_held", "premium_collected",
                   "fees", "realized_pnl", "roc", "annualized_roc", "status"]].rename(
        columns={"premium_collected": "premium", "realized_pnl": "realized P&L",
                 "annualized_roc": "ann. ROC"})
    view = to_display_currency(view, ["premium", "fees", "realized P&L"])
    st.dataframe(view.sort_values("close_time", ascending=False),
                 use_container_width=True, hide_index=True)
    note = _table_currency_note()
    if note:
        st.caption(note)


def tab_cycles(deals):
    st.subheader("Wheel cycles by ticker")
    cycles = wheel.build_cycles(deals)
    if cycles.empty:
        st.info("No trades yet.")
        return
    cycles = to_display_currency(
        cycles, ["option_premium", "stock_pnl", "fees", "net_pnl"])
    st.dataframe(cycles, use_container_width=True, hide_index=True)
    st.caption(_table_currency_note(
        "share_balance ≠ 0 means a wheel is still open (you hold/owe shares)."))


def tab_manual(deals):
    st.subheader("Add a trade manually")
    st.caption("Use this for trades when OpenD is off, or to record assignments/fees.")

    TYPES = {
        "Sell Put (CSP)": ("option", "sell", "P"),
        "Sell Call (CC)": ("option", "sell", "C"),
        "Buy to Close (Put)": ("option", "buy", "P"),
        "Buy to Close (Call)": ("option", "buy", "C"),
        "Stock Buy (assignment)": ("stock", "buy", None),
        "Stock Sell (called away)": ("stock", "sell", None),
    }

    with st.form("manual_form"):
        c1, c2, c3 = st.columns(3)
        ttype = c1.selectbox("Trade type", list(TYPES.keys()))
        underlying = c2.text_input("Underlying", "").upper()
        tdate = c3.date_input("Trade date", date.today())

        sec_type, side, opt_type = TYPES[ttype]
        c1, c2, c3, c4 = st.columns(4)
        qty = c1.number_input("Qty (contracts/shares)", min_value=0.0, value=1.0, step=1.0)
        price = c2.number_input("Price per share", min_value=0.0, value=0.0, step=0.01)
        if sec_type == "option":
            strike = c3.number_input("Strike", min_value=0.0, value=0.0, step=0.5)
            expiry = c4.date_input("Expiry", date.today())
        else:
            strike, expiry = None, None
        fee = st.number_input("Fee", min_value=0.0,
                              value=float(CFG.fees.per_contract * qty if sec_type == "option" else 0.0),
                              step=0.25)
        notes = st.text_input("Notes", "")

        if st.form_submit_button("Add trade"):
            if not underlying:
                st.error("Underlying is required.")
            else:
                if sec_type == "option":
                    code = build_option_code(underlying, expiry, opt_type, strike, CFG.moomoo.trd_market)
                    premium = wheel.gross_premium(side, qty, price)
                else:
                    code = f"{CFG.moomoo.trd_market}.{underlying}"
                    premium = 0.0
                db.upsert_deals([{
                    "deal_id": f"manual-{uuid.uuid4().hex[:12]}",
                    "order_id": "", "code": code, "underlying": underlying,
                    "sec_type": sec_type, "opt_type": opt_type, "strike": strike,
                    "expiry": expiry.isoformat() if expiry else None, "side": side,
                    "qty": qty, "price": price, "premium": premium, "fee": fee,
                    "trade_time": datetime.combine(tdate, datetime.min.time()).isoformat(),
                    "source": "manual",
                    "wheel_event": wheel.classify_event(side, sec_type, opt_type),
                    "cycle_id": None, "status": "manual", "notes": notes,
                }])
                st.success(f"Added {ttype} {underlying}.")
                st.rerun()

    st.divider()
    st.subheader("Tag an assignment")
    st.caption("Mark a short option as assigned so its wheel cycle links to the stock leg.")
    if not deals.empty:
        short_opts = deals[(deals["sec_type"] == "option") & (deals["side"] == "sell")]
        codes = sorted(short_opts["code"].unique())
        if codes:
            c1, c2 = st.columns([3, 1])
            pick = c1.selectbox("Short option", codes)
            if c2.button("Mark assigned"):
                db.update_deals_by_code(pick, wheel_event="assigned")
                st.success(f"Marked {pick} assigned.")
                st.rerun()

    st.divider()
    st.subheader("Delete a deal")
    if not deals.empty:
        c1, c2 = st.columns([3, 1])
        did = c1.selectbox("Deal", deals["deal_id"].tolist())
        if c2.button("Delete", type="secondary"):
            db.delete_deal(did)
            st.success("Deleted.")
            st.rerun()

    with st.expander("Show all raw deals"):
        st.dataframe(deals, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    auth.require_login()
    tickers = sidebar()

    deals = db.get_deals()
    if not deals.empty and tickers:
        deals = deals[deals["underlying"].isin(tickers)].reset_index(drop=True)

    legs = wheel.build_option_legs(deals)
    account = db.latest_account_snapshot()
    positions = db.get_positions()

    if deals.empty:
        st.info(
            "No trades yet. Run the sync agent on your PC, or add trades in the "
            "**Manual** tab." if CLOUD else
            "No trades yet. Click **🔄 Sync from moomoo** (with OpenD running) "
            "or add trades in the **Manual** tab."
        )

    t1, t2, t3, t4, t5, t6, t7, t8 = st.tabs(
        ["📊 Overview", "📈 Performance", "🧠 Strategy", "📌 Open Positions",
         "🧾 Trade History", "🛞 Wheel Cycles", "📓 Journal", "✍️ Manual"])
    with t1:
        tab_overview(deals, legs, account, positions)
    with t2:
        tab_performance(legs)
    with t3:
        tab_strategy(deals, legs)
    with t4:
        tab_open_positions(legs, positions)
    with t5:
        tab_history(legs)
    with t6:
        tab_cycles(deals)
    with t7:
        tab_journal(legs)
    with t8:
        tab_manual(deals)


if __name__ == "__main__":
    main()
