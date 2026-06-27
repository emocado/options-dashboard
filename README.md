# Options Wheel Dashboard 🛞

A personal dashboard to track the **wheel strategy** (cash-secured puts → covered
calls) on **moomoo**. It can auto-sync trades from your moomoo account via the
**moomoo OpenD** gateway, and also works fully offline with manual entry.

It is built with **Streamlit + SQLite** (all Python) and computes wheel-specific
metrics: premium collected, realized/unrealized P&L, return on capital,
annualized ROC, win rate, capital deployed, and per-ticker wheel cycles.

---

## Quick start

```bash
pip install -r requirements-local.txt
python -m streamlit run app.py
```

Then open the URL it prints (default http://localhost:8501). The first run
creates an empty `data/dashboard.db`. Add trades in the **Manual** tab, or set up
moomoo sync below.

> On Windows, if the `streamlit` command isn't on your PATH, always use
> `python -m streamlit run app.py`.

**Want it on your phone?** See [DEPLOY.md](DEPLOY.md) for two options, both free:
- **Tailscale** — keep everything on your PC (max privacy); view over a private VPN.
- **Cloud hosting** — host the UI on Streamlit Community Cloud reading from a free
  Turso DB, with a small sync agent on your PC. View anytime, even with the PC asleep.

Either way, set a dashboard password first with `python tools/set_password.py`.

---

## Connecting to moomoo (optional auto-sync)

The dashboard pulls data from **moomoo OpenD**, a small gateway app that runs on
your computer and relays requests to moomoo. **One-time setup:**

1. **Install moomoo OpenD** — download from
   <https://www.moomoo.com/download/OpenAPI>, install, and **log in** with your
   moomoo account.
2. **Unlock trading in the OpenD GUI.** The API cannot unlock trading for
   security reasons — you must toggle it in OpenD itself.
3. Make sure OpenD is listening on `127.0.0.1:11111` (the default).
4. In the dashboard sidebar → **⚙️ Settings**, set:
   - **Security firm**: `FUTUSG` (Moomoo Singapore). Use `FUTUINC` for US,
     `FUTUSECURITIES` for HK, `FUTUAU` for Australia.
   - **Trade market**: `US` (for US options).
   - **Trade env**: start with `SIMULATE` (paper) to validate, then `REAL`.
5. Click **🔌 Test connection → Ping OpenD**. When it succeeds, click
   **🔄 Sync from moomoo**.

Settings are stored in `config.toml` (copy from `config.example.toml`). They can
also be edited from the Settings panel.

### Notes & limitations
- **Fees:** moomoo's deal data does **not** include commissions/fees, so net P&L
  uses an estimated **fee per contract** (configurable in Settings, overridable
  per trade in Manual entry). For exact numbers, enter fees from your statement.
- **Assignments / called-away:** these show up as stock buys/sells. The app can't
  always tell an assignment from a normal stock trade, so use **Manual → Tag an
  assignment** to link a short option to its stock leg and complete the wheel
  cycle.
- **Expired options:** inferred (a short option past expiry with no buy-to-close
  is treated as expired worthless, full credit kept).
- **OpenD must be running** for sync. Without it, the dashboard still works from
  whatever is already in the database plus manual entry.

---

## What each tab shows

| Tab | Contents |
|-----|----------|
| **📊 Overview** | KPI cards (premium collected, net realized P&L, unrealized P&L, win rate, premium this week/month/YTD, annualized ROC, capital secured, % deployed, account NAV/cash/power) + charts (cumulative realized P&L, monthly premium, P&L by ticker, open capital allocation). |
| **📌 Open Positions** | Open CSP/CC legs with strike, expiry, DTE, premium, capital secured, current value, unrealized P&L (from last sync), annualized ROC. |
| **🧾 Trade History** | Closed legs with realized P&L, days held, ROC, annualized ROC, outcome (closed / expired / assigned). |
| **🛞 Wheel Cycles** | Per-ticker rollup: option premium, stock P&L, fees, net P&L, share balance (≠ 0 means the wheel is still open). |
| **✍️ Manual** | Add trades by hand, tag assignments, delete deals, and inspect raw data. |

---

## Metric definitions

- **Return on capital (ROC)** = net premium ÷ (strike × 100 × contracts).
- **Annualized ROC** = ROC × 365 ÷ days held (uses days-to-expiry for open legs).
- **Net realized P&L** = realized option P&L (closed/expired/assigned legs) +
  realized stock P&L (assignments/called-away) − fees.
- **Capital secured** = Σ strike × 100 × contracts for open **cash-secured puts**
  (covered calls are backed by shares already in your net asset, so they're not
  counted as cash deployed).
- **Net realized P&L** counts stock gains only on shares actually sold
  (called away); assigned shares you still hold sit in unrealized P&L, not as a loss.
- **Win rate** = share of closed legs with positive realized P&L.

---

## Project layout

```
app.py                 Streamlit UI (sidebar + 5 tabs)
src/config.py          Settings load/save (config.toml)
src/db.py              SQLite schema + helpers
src/options.py         OCC option-code parser/builder
src/wheel.py           Wheel classification, legs, cycles (pure)
src/metrics.py         Portfolio KPIs and chart data (pure)
src/moomoo_client.py   moomoo OpenD sync (lazy SDK import)
tests/                 pytest unit tests
```

Run tests with:

```bash
python -m pytest -q
```

---

## Disclaimer

This is a personal tracking tool, not financial advice. Numbers depend on the
data synced/entered and on fee estimates. Always reconcile against your official
moomoo statements before relying on them.
