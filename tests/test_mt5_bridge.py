import mt5_bridge


def test_bridge_rejects_unsupported_timeframe():
    client = mt5_bridge.app.test_client()
    response = client.get("/ohlcv?symbol=SFX_VOL_75&timeframe=3m")
    assert response.status_code == 400
    assert "unsupported timeframe" in response.get_json()["error"]


def test_bridge_rejects_unbounded_count():
    client = mt5_bridge.app.test_client()
    response = client.get("/ohlcv?symbol=SFX_VOL_75&timeframe=5m&count=2001")
    assert response.status_code == 400
    assert "between 1 and 2000" in response.get_json()["error"]


def test_bridge_rejects_non_integer_count():
    client = mt5_bridge.app.test_client()
    response = client.get("/ohlcv?symbol=SFX_VOL_75&timeframe=5m&count=lots")
    assert response.status_code == 400
    assert "integer" in response.get_json()["error"]


def test_bridge_requires_token_for_non_loopback_binding(monkeypatch):
    monkeypatch.setattr(mt5_bridge, "MT5_BRIDGE_HOST", "0.0.0.0")
    monkeypatch.setattr(mt5_bridge, "MT5_BRIDGE_TOKEN", "")
    client = mt5_bridge.app.test_client()
    response = client.get("/ohlcv?symbol=SFX_VOL_75&timeframe=5m")
    assert response.status_code == 401


def test_position_zero_marks_only_latest_returned_bar_forming():
    assert mt5_bridge._bar_status(0, 2, 0) == "closed"
    assert mt5_bridge._bar_status(1, 2, 0) == "closed"
    assert mt5_bridge._bar_status(2, 2, 0) == "forming"


def test_historical_pagination_marks_every_returned_bar_closed():
    assert mt5_bridge._bar_status(0, 2, 300) == "closed"
    assert mt5_bridge._bar_status(1, 2, 300) == "closed"
    assert mt5_bridge._bar_status(2, 2, 300) == "closed"
