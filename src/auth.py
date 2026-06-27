"""Password gate for the dashboard (defense-in-depth on top of the network layer).

The password is never stored in plaintext. A salted **PBKDF2-HMAC-SHA256** hash
is kept in `.streamlit/secrets.toml` as:

    APP_PASSWORD_HASH = "pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>"

Generate/update it with:  python tools/set_password.py
"""

from __future__ import annotations

import hashlib
import hmac
import os

import streamlit as st


def hash_password(password: str, iterations: int = 240_000,
                  salt: bytes | None = None) -> str:
    """Return a `pbkdf2_sha256$iters$salt$hash` string for `password`."""
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def _stored_hash() -> str | None:
    """Read the configured hash from st.secrets, falling back to an env var."""
    try:
        val = st.secrets.get("APP_PASSWORD_HASH")
        if val:
            return str(val)
    except Exception:
        pass
    return os.environ.get("APP_PASSWORD_HASH")


def require_login() -> None:
    """Block the app until a correct password is entered. Call first in main()."""
    expected = _stored_hash()
    if not expected:
        st.title("🔒 Setup required")
        st.error(
            "No app password is configured. From the project folder, run:\n\n"
            "`python tools/set_password.py`\n\n"
            "then refresh this page. See DEPLOY.md for details."
        )
        st.stop()

    if st.session_state.get("authed"):
        return

    st.title("🔒 Sign in")
    st.caption("Enter your dashboard password.")
    with st.form("login"):
        pw = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")
    if submitted:
        if verify_password(pw, expected):
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()
