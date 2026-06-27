"""Local sync agent: pull from moomoo OpenD and push to the cloud (Turso).

Designed to run unattended from Windows Task Scheduler. It:
  1. loads the Turso connection from ``.streamlit/secrets.toml`` into the env,
  2. runs the same sync the dashboard's button runs (``sync_service``), and
  3. writes the results to the remote Turso DB.

When OpenD isn't running (moomoo not logged in, or session closed) it logs a line
and exits 0 — so a scheduled run is never spuriously marked as failed. A missing
moomoo SDK or unexpected error exits non-zero so Task Scheduler surfaces it.

    python tools/sync_to_cloud.py
"""

from __future__ import annotations

import os
import sys
import tomllib
from datetime import datetime
from pathlib import Path

# Make `src` importable when run as `python tools/sync_to_cloud.py`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SECRETS_PATH = ROOT / ".streamlit" / "secrets.toml"
TURSO_KEYS = ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN")


def _log(msg: str) -> None:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)


def _load_turso_env() -> None:
    """Copy Turso creds from secrets.toml into os.environ (env wins if already set)."""
    if SECRETS_PATH.exists():
        with SECRETS_PATH.open("rb") as fh:
            secrets = tomllib.load(fh)
        for key in TURSO_KEYS:
            if key in secrets and not os.environ.get(key):
                os.environ[key] = str(secrets[key])
    if not os.environ.get("TURSO_DATABASE_URL"):
        _log(
            "WARNING: no TURSO_DATABASE_URL found — writing to the LOCAL db only. "
            "Add Turso creds to .streamlit/secrets.toml to push to the cloud."
        )


def main() -> int:
    _load_turso_env()
    from src import db, moomoo_client, sync_service
    from src.config import load_config

    cfg = load_config()
    db.init_db()
    try:
        messages = sync_service.sync_and_persist(cfg)
    except moomoo_client.OpenDUnreachable as exc:
        _log(f"OpenD not reachable — skipping this run. ({exc})")
        return 0
    except moomoo_client.MoomooUnavailable as exc:
        _log(f"moomoo SDK unavailable: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        _log(f"Sync failed: {exc}")
        return 1

    for msg in messages:
        _log(msg)
    _log("Sync complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
