"""Live FX rates for display-currency conversion.

The dashboard stores money in the account's base currency (USD for US-market
wheels). When the user toggles the display to SGD we apply a live USD->SGD rate
fetched from the free Frankfurter API (ECB reference rates, no API key). If the
network call fails we fall back to a recent static rate so the UI still renders.
"""

from __future__ import annotations

import httpx

# Recent static fallbacks (base -> quote) used when the live fetch fails.
# Keyed by quote currency, assuming a USD base.
FALLBACK_RATES = {"SGD": 1.35, "USD": 1.0}

_API_URL = "https://api.frankfurter.app/latest"


def fetch_rate(base: str, quote: str, timeout: float = 5.0) -> tuple[float, bool]:
    """Return ``(rate, is_live)`` for converting a ``base`` amount to ``quote``.

    ``rate`` multiplies a ``base`` amount to get the ``quote`` amount. On any
    network or parse error it returns a static fallback with ``is_live=False``
    so callers can still render (and flag the figure as approximate).
    """
    base, quote = base.upper(), quote.upper()
    if base == quote:
        return 1.0, True
    try:
        resp = httpx.get(_API_URL, params={"from": base, "to": quote}, timeout=timeout)
        resp.raise_for_status()
        rate = float(resp.json()["rates"][quote])
        if rate > 0:
            return rate, True
    except Exception:  # noqa: BLE001 - any failure falls back to a static rate
        pass
    return FALLBACK_RATES.get(quote, 1.0), False
