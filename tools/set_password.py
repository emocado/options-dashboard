"""Set (or change) the dashboard login password.

Writes a salted PBKDF2 hash to .streamlit/secrets.toml as APP_PASSWORD_HASH.
The plaintext password is never stored. Run from the project root:

    python tools/set_password.py

You'll be prompted for the password (input is hidden).
"""

from __future__ import annotations

import getpass
import sys
import tomllib
from pathlib import Path

# Make `src` importable when run as `python tools/set_password.py`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.auth import hash_password  # noqa: E402

SECRETS_PATH = ROOT / ".streamlit" / "secrets.toml"
MIN_LEN = 10


def main() -> None:
    pw = getpass.getpass("New dashboard password: ")
    if len(pw) < MIN_LEN:
        sys.exit(f"Password must be at least {MIN_LEN} characters. Nothing changed.")
    if pw != getpass.getpass("Confirm password: "):
        sys.exit("Passwords did not match. Nothing changed.")

    # Preserve any other existing secrets.
    existing: dict = {}
    if SECRETS_PATH.exists():
        with SECRETS_PATH.open("rb") as fh:
            existing = tomllib.load(fh)
    existing["APP_PASSWORD_HASH"] = hash_password(pw)

    SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'{k} = "{v}"' for k, v in existing.items() if not isinstance(v, dict)]
    SECRETS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Password saved to {SECRETS_PATH}. Restart the app to use it.")


if __name__ == "__main__":
    main()
