"""SQLite / libSQL persistence for the Options Wheel Dashboard.

One row per fill lives in `deals` (from moomoo sync or manual entry). Account
net-asset snapshots and the latest open-positions cache are stored separately so
the dashboard still shows numbers when OpenD is offline.

Two backends, chosen by environment:
  * Default — a local SQLite file at ``DB_PATH`` (dev, offline, tests).
  * Turso (libSQL) — when ``TURSO_DATABASE_URL`` is set, connect to a remote
    libSQL database instead. This lets a cloud-hosted UI and the local sync
    agent share one source of truth. Both backends speak DB-API 2.0 (``?``
    placeholders, ``execute``/``executemany``/``fetchall``), so the queries
    below are identical; only the connection differs.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

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

CREATE TABLE IF NOT EXISTS journal (
    code             TEXT PRIMARY KEY,  -- option contract code (one entry per leg)
    thesis           TEXT,              -- reasoning captured at open
    execution_rating INTEGER,           -- 1..5 (Sloppy..Textbook), set at close
    review_note      TEXT,
    created_at       TEXT,
    updated_at       TEXT
);
"""

DEAL_COLUMNS = [
    "deal_id", "order_id", "code", "underlying", "sec_type", "opt_type",
    "strike", "expiry", "side", "qty", "price", "premium", "fee",
    "trade_time", "source", "wheel_event", "cycle_id", "status", "notes",
]

JOURNAL_COLUMNS = [
    "code", "thesis", "execution_rating", "review_note", "created_at", "updated_at",
]


def _ensure_data_dir() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


def _using_turso() -> bool:
    return bool(os.environ.get("TURSO_DATABASE_URL"))


def _connect():
    """Open a backend connection: remote libSQL if configured, else local SQLite."""
    url = os.environ.get("TURSO_DATABASE_URL")
    if url:
        try:
            import libsql  # noqa: WPS433 (optional dep)
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError(
                "TURSO_DATABASE_URL is set but libsql is not installed. "
                "Run `pip install libsql`."
            ) from exc
        # Remote-only connection: pass the libsql:// URL as the database name and
        # the auth token. (No sync_url => no embedded replica, so the cloud
        # container stays stateless and writes persist server-side immediately.)
        return libsql.connect(database=url, auth_token=os.environ.get("TURSO_AUTH_TOKEN"))

    _ensure_data_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_conn() -> Iterator[Any]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _read_df(conn, sql: str, params: tuple = ()) -> pd.DataFrame:
    """Run a SELECT and return a DataFrame, backend-agnostically.

    Avoids pandas' ``read_sql_query`` backend detection (which special-cases
    sqlite3 / SQLAlchemy and doesn't recognise libSQL connections).
    """
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = [tuple(r) for r in cur.fetchall()]
    return pd.DataFrame(rows, columns=cols)


def init_db() -> None:
    # Execute statements one at a time: libSQL has no multi-statement
    # ``executescript``, and single ``execute`` works on both backends.
    statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]
    with get_conn() as conn:
        for stmt in statements:
            conn.execute(stmt)


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
        df = _read_df(conn, "SELECT * FROM deals")
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
        cur = conn.execute(
            "SELECT * FROM account_snapshots ORDER BY ts DESC LIMIT 1"
        )
        cols = [d[0] for d in cur.description] if cur.description else []
        row = cur.fetchone()
    return dict(zip(cols, tuple(row))) if row else None


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
        return _read_df(conn, "SELECT * FROM positions_snapshot")


# ---------------------------------------------------------------------------
# Trade journal (thesis at open, execution rating at close)
# ---------------------------------------------------------------------------

def upsert_journal(code: str, **fields) -> None:
    """Create or patch the journal entry for one option leg, keyed on ``code``.

    Merges into any existing row (so logging a thesis, then later a rating, both
    persist) and always refreshes ``updated_at``.
    """
    from datetime import datetime

    existing = get_journal_entry(code) or {}
    merged = {**existing, **{k: v for k, v in fields.items() if k in JOURNAL_COLUMNS}}
    merged["code"] = code
    merged.setdefault("created_at", existing.get("created_at")
                      or datetime.now().isoformat(timespec="seconds"))
    merged["updated_at"] = datetime.now().isoformat(timespec="seconds")
    row = tuple(merged.get(col) for col in JOURNAL_COLUMNS)
    placeholders = ", ".join("?" for _ in JOURNAL_COLUMNS)
    cols = ", ".join(JOURNAL_COLUMNS)
    with get_conn() as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO journal ({cols}) VALUES ({placeholders})", row
        )


def get_journal() -> pd.DataFrame:
    with get_conn() as conn:
        return _read_df(conn, "SELECT * FROM journal")


def get_journal_entry(code: str) -> dict | None:
    with get_conn() as conn:
        cur = conn.execute("SELECT * FROM journal WHERE code = ?", (code,))
        cols = [d[0] for d in cur.description] if cur.description else []
        row = cur.fetchone()
    return dict(zip(cols, tuple(row))) if row else None
