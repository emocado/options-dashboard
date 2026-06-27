"""Headless sync orchestration shared by the Streamlit UI and the cloud agent.

Pulls deals/positions/account from moomoo OpenD via ``moomoo_client.sync`` and
persists them to whichever DB backend ``db`` is configured for (local SQLite or
remote Turso). Intentionally free of any Streamlit import, so the same code path
runs from a scheduled task (`tools/sync_to_cloud.py`).
"""

from __future__ import annotations

from datetime import datetime

from . import db, moomoo_client
from .config import Config


def sync_and_persist(cfg: Config) -> list[str]:
    """Run a moomoo sync and write the results to the database.

    Returns the human-readable messages from the sync (row counts etc.).
    Propagates ``moomoo_client.MoomooUnavailable`` / ``OpenDUnreachable`` so
    callers decide how to report them (sidebar warning vs. log line).
    """
    result = moomoo_client.sync(cfg)
    if result.deals:
        db.upsert_deals(result.deals)
    db.replace_positions(result.positions)
    if result.account:
        db.add_account_snapshot(
            datetime.now().isoformat(timespec="seconds"),
            result.account["net_asset"],
            result.account["cash"],
            result.account["power"],
        )
    return result.messages
