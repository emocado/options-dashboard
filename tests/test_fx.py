from src import fx


def test_same_currency_is_identity():
    assert fx.fetch_rate("USD", "USD") == (1.0, True)


def test_live_rate_parsed(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"rates": {"SGD": 1.3520}}

    monkeypatch.setattr(fx.httpx, "get", lambda *a, **k: FakeResp())
    rate, is_live = fx.fetch_rate("USD", "SGD")
    assert is_live is True
    assert rate == 1.3520


def test_network_failure_falls_back(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(fx.httpx, "get", boom)
    rate, is_live = fx.fetch_rate("USD", "SGD")
    assert is_live is False
    assert rate == fx.FALLBACK_RATES["SGD"]
