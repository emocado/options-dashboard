"""One-time migration: copy the local SQLite dashboard into Turso.

Unlike re-running a moomoo sync, this preserves everything in your local DB —
manual entries, assignment tags, and per-trade fee edits — by copying rows
verbatim. Safe to re-run: writes use INSERT OR REPLACE on the same keys.

    python tools/migrate_to_turso.py

Reads Turso creds from .streamlit/secrets.toml (or the environment).
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tomllib
from pathlib import Path

# Make `src` importable when run as `python tools/migrate_to_turso.py`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SECRETS_PATH = ROOT / ".streamlit" / "secrets.toml"
TURSO_KEYS = ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN")


def _load_turso_env() -> None:
    if SECRETS_PATH.exists():
        with SECRETS_PATH.open("rb") as fh:
            secrets = tomllib.load(fh)
        for key in TURSO_KEYS:
            if key in secrets and not os.environ.get(key):
                os.environ[key] = str(secrets[key])


def _read_local(table: str) -> list[dict]:
    """Read a table straight from the local SQLite file (never via Turso)."""
    from src.config import DB_PATH

    if not Path(DB_PATH).exists():
        return []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608 (fixed names)
    except sqlite3.OperationalError:
        rows = []  # table doesn't exist yet
    finally:
        conn.close()
    return [dict(r) for r in rows]


def main() -> int:
    _load_turso_env()
    if not os.environ.get("TURSO_DATABASE_URL"):
        sys.exit(
            "No TURSO_DATABASE_URL found in the environment or "
            ".streamlit/secrets.toml. Add your Turso creds first (see DEPLOY.md)."
        )

    deals = _read_local("deals")
    snapshots = _read_local("account_snapshots")
    positions = _read_local("positions_snapshot")
    print(
        f"Local DB: {len(deals)} deals, {len(snapshots)} account snapshots, "
        f"{len(positions)} positions."
    )
    if not (deals or snapshots or positions):
        sys.exit("Nothing to migrate (local DB empty or missing).")

    # Import db only after the env is set, so it targets the remote Turso backend.
    from src import db

    db.init_db()
    written = db.upsert_deals(deals)
    for s in snapshots:
        db.add_account_snapshot(s["ts"], s["net_asset"], s["cash"], s["power"])
    db.replace_positions(positions)
    print(
        f"Migrated {written} deals, {len(snapshots)} snapshots, "
        f"{len(positions)} positions to Turso."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
