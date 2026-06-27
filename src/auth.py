"""Authentication for the dashboard.

Two modes, picked automatically by what's configured in secrets:

  * **Google sign-in (OpenID Connect)** — used when an ``[auth]`` section is present
    (the cloud deployment). Built on Streamlit's native ``st.login`` / ``st.user``.
    Access is further restricted to an **email allowlist** (``ALLOWED_EMAILS``) so
    only you can get in — anyone can *authenticate* with Google, but only allowlisted,
    email-verified accounts are let *through*.

  * **Password gate (PBKDF2)** — fallback for local/offline use when ``[auth]`` is not
    configured. Hash is created with ``python tools/set_password.py`` and stored in
    ``.streamlit/secrets.toml`` as ``APP_PASSWORD_HASH``.
"""

from __future__ import annotations

import hashlib
import hmac
import os

import streamlit as st


# ---------------------------------------------------------------------------
# Password hashing (used by the password fallback and tools/set_password.py)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Mode detection & allowlist
# ---------------------------------------------------------------------------

def _oidc_configured() -> bool:
    """True if Streamlit native auth (an ``[auth]`` block) is set up in secrets."""
    try:
        auth_cfg = st.secrets.get("auth")
    except Exception:
        return False
    # Needs at least redirect_uri + cookie_secret to function.
    return bool(auth_cfg and auth_cfg.get("redirect_uri") and auth_cfg.get("cookie_secret"))


def _allowed_emails() -> set[str]:
    """Lower-cased set of emails permitted to sign in (from secrets or env var)."""
    raw = None
    try:
        raw = st.secrets.get("ALLOWED_EMAILS")
    except Exception:
        raw = None
    if not raw:
        raw = os.environ.get("ALLOWED_EMAILS")
    if not raw:
        return set()
    return {e.strip().lower() for e in str(raw).split(",") if e.strip()}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def require_login() -> None:
    """Block the app until the visitor is authenticated AND authorized.

    Call first in main(). Uses Google sign-in when configured, else the password.
    """
    if _oidc_configured():
        _require_google_login()
    else:
        _require_password()


def logout() -> None:
    """Sign the current user out, for whichever mode is active."""
    if _oidc_configured():
        st.logout()
    else:
        st.session_state["authed"] = False
        st.rerun()


def current_email() -> str | None:
    """Email of the signed-in Google user, if any (None in password mode)."""
    if not _oidc_configured():
        return None
    try:
        if st.user.is_logged_in:
            return st.user.email
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Google sign-in (OIDC) + allowlist
# ---------------------------------------------------------------------------

def _require_google_login() -> None:
    if not st.user.is_logged_in:
        st.title("🔒 Sign in")
        st.caption("Sign in with your Google account to access the dashboard.")
        if st.button("Sign in with Google", type="primary"):
            st.login()
        st.stop()

    allowed = _allowed_emails()
    email = (st.user.email or "").lower()
    # Default to verified=True if the provider didn't send the claim; Google does.
    verified = getattr(st.user, "email_verified", True)

    if not allowed:
        st.title("🚫 Access locked")
        st.error(
            "No `ALLOWED_EMAILS` is configured, so sign-in is locked for everyone. "
            "Add your email to the app's secrets (see DEPLOY.md)."
        )
        if st.button("Sign out"):
            st.logout()
        st.stop()

    if email not in allowed or not verified:
        st.title("🚫 Access denied")
        st.error(f"`{st.user.email}` is not authorized to view this dashboard.")
        if st.button("Sign out"):
            st.logout()
        st.stop()


# ---------------------------------------------------------------------------
# Password gate (fallback)
# ---------------------------------------------------------------------------

def _require_password() -> None:
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
