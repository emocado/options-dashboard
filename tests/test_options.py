from datetime import date

from src.options import parse_option_code, is_option_code, underlying_of


def test_parse_put_with_market_prefix():
    c = parse_option_code("US.AAPL250620P00150000")
    assert c is not None
    assert c.underlying == "AAPL"
    assert c.expiry == date(2025, 6, 20)
    assert c.opt_type == "P"
    assert c.strike == 150.0
    assert c.market == "US"
    assert c.is_put and not c.is_call


def test_parse_call_without_prefix():
    c = parse_option_code("TSLA260116C00250000")
    assert c.underlying == "TSLA"
    assert c.expiry == date(2026, 1, 16)
    assert c.opt_type == "C"
    assert c.strike == 250.0
    assert c.market is None


def test_fractional_strike():
    c = parse_option_code("US.SPY250620P00450500")
    assert c.strike == 450.5


def test_moomoo_unpadded_strike():
    # moomoo writes the strike WITHOUT OCC zero-padding (260000, not 00260000).
    c = parse_option_code("US.AMZN260807C260000")
    assert c is not None
    assert c.underlying == "AMZN"
    assert c.expiry == date(2026, 8, 7)
    assert c.opt_type == "C"
    assert c.strike == 260.0


def test_moomoo_five_letter_underlying():
    c = parse_option_code("US.GOOGL260807C380000")
    assert c.underlying == "GOOGL"
    assert c.strike == 380.0
    assert c.is_call


def test_build_code_roundtrips_with_parser():
    code = __import__("src.options", fromlist=["build_option_code"]).build_option_code(
        "AMZN", date(2026, 8, 7), "C", 260.0)
    c = parse_option_code(code)
    assert c.underlying == "AMZN" and c.strike == 260.0 and c.opt_type == "C"


def test_is_option_code():
    assert is_option_code("US.AAPL250620P00150000")
    assert not is_option_code("US.AAPL")
    assert not is_option_code("AAPL")


def test_underlying_of():
    assert underlying_of("US.AAPL") == "AAPL"
    assert underlying_of("AAPL") == "AAPL"
    assert underlying_of("US.AAPL250620P00150000") == "AAPL"


def test_non_option_returns_none():
    assert parse_option_code("US.AAPL") is None
