"""Parse moomoo / OCC-style option codes into structured fields.

moomoo returns US option symbols in OCC format, optionally prefixed with the
market, e.g.:

    US.AAPL250620P00150000   ->  AAPL 2025-06-20 PUT  strike 150.0
    TSLA260116C00250000      ->  TSLA 2026-01-16 CALL strike 250.0

Layout after the (variable-length) underlying:
    YYMMDD  (expiry)
    C or P  (call/put)
    strike in thousandths of a dollar.

NOTE: moomoo does NOT zero-pad the strike to the OCC-standard 8 digits — it
writes ``260000`` (= $260.000), not ``00260000``. We therefore accept a
variable-length digit run for the strike, which also handles the padded form
(``00150000`` -> 150.0) since we divide by 1000 either way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# Underlying: 1-6 alphanumerics. Then 6-digit date, C/P, variable-length strike.
# The single C/P letter and trailing all-digit strike make the split deterministic.
_OCC_RE = re.compile(
    r"^(?:(?P<market>[A-Z]{2})\.)?"
    r"(?P<underlying>[A-Z0-9]{1,6})"
    r"(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})"
    r"(?P<cp>[CP])"
    r"(?P<strike>\d+)$"
)


@dataclass(frozen=True)
class OptionContract:
    underlying: str
    expiry: date
    opt_type: str  # "P" or "C"
    strike: float
    market: str | None = None

    @property
    def is_put(self) -> bool:
        return self.opt_type == "P"

    @property
    def is_call(self) -> bool:
        return self.opt_type == "C"


def is_option_code(code: str) -> bool:
    """True if `code` looks like an OCC option symbol (vs. a plain stock)."""
    return _OCC_RE.match(code.strip().upper()) is not None


def parse_option_code(code: str) -> OptionContract | None:
    """Parse an OCC option code. Returns None if it is not an option symbol."""
    match = _OCC_RE.match(code.strip().upper())
    if not match:
        return None

    yy = int(match.group("yy"))
    # OCC two-digit years map to 2000-2099, which covers all listed options.
    year = 2000 + yy
    expiry = date(year, int(match.group("mm")), int(match.group("dd")))
    strike = int(match.group("strike")) / 1000.0

    return OptionContract(
        underlying=match.group("underlying"),
        expiry=expiry,
        opt_type=match.group("cp"),
        strike=strike,
        market=match.group("market"),
    )


def build_option_code(underlying: str, expiry: date, opt_type: str,
                      strike: float, market: str = "US") -> str:
    """Construct a moomoo option code from parts (inverse of parse).

    Uses moomoo's unpadded strike (e.g. AMZN...C260000) so manually entered
    trades share the same code as synced fills for the same contract.
    """
    yymmdd = expiry.strftime("%y%m%d")
    strike_int = int(round(strike * 1000))
    return f"{market}.{underlying.upper()}{yymmdd}{opt_type.upper()}{strike_int}"


def underlying_of(code: str) -> str:
    """Best-effort underlying ticker for any code (option or stock).

    For stocks the moomoo code is like "US.AAPL"; we strip the market prefix.
    """
    contract = parse_option_code(code)
    if contract:
        return contract.underlying
    cleaned = code.strip().upper()
    return cleaned.split(".")[-1] if "." in cleaned else cleaned
