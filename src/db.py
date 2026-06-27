"""SQLite persistence for the Options Wheel Dashboard.

One row per fill lives in `deals` (from moomoo sync or manual entry). Account
net-asset snapshots and the latest open-positions cache are stored separately so
the dashboard still shows numbers when OpenD is offline.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

import pandas as pd

from .config import DB_PATH

OPTION_MULTIPLIER = 100  # US listed options: 1 contract = 100 shares.

SCHEMA = """
CREATE TABLE IF NOT EXISTS deals (
    deal_id     TEXT PRIMARY KEY,
    order_id    TEXT,
    code        TEXT,
    underlying  TEXT,
    sec_type    TEXT,              -- 'option' | 'stock'
    opt_type    TEXT,              -- 'P' | 'C' | NULL
    strike      REAL,
    expiry      TEXT,              -- ISO date
    side        TEXT,              -- 'buy' | 'sell'
    qty         REAL,              -- abs contracts (option) or shares (stock)
    price       REAL,              -- per share
    premium     REAL,              -- signed gross option cash flow (+credit/-debit)
    fee         REAL DEFAULT 0,
    trade_time  TEXT,              -- ISO datetime
    source      TEXT,              -- 'moomoo' | 'manual'
    wheel_event TEXT,              -- sell_put|sell_call|buy_to_close|assigned|expired|stock_buy|stock_sell
    cycle_id    TEXT,
    status      TEXT,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS account_snapshots (
    ts         TEXT PRIMARY KEY,
    net_asset  REAL,
    cash       REAL,
    power      REAL
);

CREATE TABLE IF NOT EXISTS positions_snapshot (
    code        TEXT PRIMARY KEY,
    underlying  TEXT,
    sec_type    TEXT,
    qty         REAL,
    cost_price  REAL,
    market_val  REAL,
    pl_val      REAL,
    pl_ratio    REAL,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

DEAL_COLUMNS = [
    "deal_id", "order_id", "code", "underlying", "sec_type", "opt_type",
    "strike", "expiry", "side", "qty", "price", "premium", "fee",
    "trade_time", "source", "wheel_event", "cycle_id", "status", "notes",
]


def _ensure_data_dir() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    _ensure_data_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Deals
# ---------------------------------------------------------------------------

def upsert_deals(deals: Iterable[dict]) -> int:
    """Insert or replace deals keyed on deal_id. Returns rows written."""
    rows = [tuple(d.get(col) for col in DEAL_COLUMNS) for d in deals]
    if not rows:
        return 0
    placeholders = ", ".join("?" for _ in DEAL_COLUMNS)
    cols = ", ".join(DEAL_COLUMNS)
    with get_conn() as conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO deals ({cols}) VALUES ({placeholders})", rows
        )
    return len(rows)


def update_deal_fields(deal_id: str, **fields) -> None:
    """Patch specific columns of one deal (used by manual edits / tagging)."""
    allowed = {k: v for k, v in fields.items() if k in DEAL_COLUMNS}
    if not allowed:
        return
    assignments = ", ".join(f"{k} = ?" for k in allowed)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE deals SET {assignments} WHERE deal_id = ?",
            (*allowed.values(), deal_id),
        )


def update_deals_by_code(code: str, **fields) -> None:
    """Patch columns for every deal sharing a contract code (e.g. tag assigned)."""
    allowed = {k: v for k, v in fields.items() if k in DEAL_COLUMNS}
    if not allowed:
        return
    assignments = ", ".join(f"{k} = ?" for k in allowed)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE deals SET {assignments} WHERE code = ?",
            (*allowed.values(), code),
        )


def delete_deal(deal_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM deals WHERE deal_id = ?", (deal_id,))


def get_deals() -> pd.DataFrame:
    """All deals as a DataFrame with typed expiry/trade_time columns."""
    with get_conn() as conn:
        df = pd.read_sql_query("SELECT * FROM deals", conn)
    if df.empty:
        return df
    df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce")
    df["trade_time"] = pd.to_datetime(df["trade_time"], errors="coerce")
    for col in ("strike", "qty", "price", "premium", "fee"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values("trade_time").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Account snapshots & positions
# ---------------------------------------------------------------------------

def add_account_snapshot(ts: str, net_asset: float, cash: float, power: float) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO account_snapshots (ts, net_asset, cash, power)"
            " VALUES (?, ?, ?, ?)",
            (ts, net_asset, cash, power),
        )


def latest_account_snapshot() -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM account_snapshots ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def replace_positions(positions: Iterable[dict]) -> None:
    """Replace the open-positions cache with a fresh snapshot."""
    cols = ["code", "underlying", "sec_type", "qty", "cost_price",
            "market_val", "pl_val", "pl_ratio", "updated_at"]
    rows = [tuple(p.get(c) for c in cols) for p in positions]
    with get_conn() as conn:
        conn.execute("DELETE FROM positions_snapshot")
        if rows:
            placeholders = ", ".join("?" for _ in cols)
            conn.executemany(
                f"INSERT OR REPLACE INTO positions_snapshot ({', '.join(cols)})"
                f" VALUES ({placeholders})",
                rows,
            )


def get_positions() -> pd.DataFrame:
    with get_conn() as conn:
        df = pd.read_sql_query("SELECT * FROM positions_snapshot", conn)
    return df
