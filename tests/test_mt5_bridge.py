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


def test_quarantine_detects_invalid_timestamp_numeric_ohlc_and_duplicates():
    rows = [
        {"time": 100, "open": 10, "high": 12, "low": 9, "close": 11,
         "tick_volume": 10, "real_volume": 0, "spread": 1},
        {"time": 0, "open": 10, "high": 12, "low": 9, "close": 11,
         "tick_volume": 10, "real_volume": 0, "spread": 1},
        {"time": 200, "open": float("nan"), "high": 12, "low": 9, "close": 11,
         "tick_volume": 10, "real_volume": 0, "spread": 1},
        {"time": 300, "open": 10, "high": 8, "low": 9, "close": 11,
         "tick_volume": 10, "real_volume": 0, "spread": 1},
        {"time": 100, "open": 10, "high": 12, "low": 9, "close": 11,
         "tick_volume": 10, "real_volume": 0, "spread": 1},
    ]
    reasons = mt5_bridge._quarantine_reasons(rows)
    reason_pairs = {(item["observation_index"], item["reason_code"]) for item in reasons}

    assert (1, "INVALID_TIMESTAMP") in reason_pairs
    assert (2, "INVALID_NUMERIC") in reason_pairs
    assert (3, "INVALID_OHLC") in reason_pairs
    assert (0, "DUPLICATE_TIMESTAMP") in reason_pairs
    assert (4, "DUPLICATE_TIMESTAMP") in reason_pairs


def test_response_row_handles_invalid_timestamp_and_non_finite_prices():
    row = {
        "time": float("nan"), "open": float("inf"), "high": 12,
        "low": 9, "close": 11, "tick_volume": 10,
        "real_volume": 0, "spread": 1,
    }
    response = mt5_bridge._response_row(row, 0, None, 0, ["INVALID_TIMESTAMP", "INVALID_NUMERIC"])
    assert response["timestamp"] is None
    assert response["Open"] is None
    assert response["bar_status"] == "closed"
    assert response["quarantine_reasons"] == ["INVALID_TIMESTAMP", "INVALID_NUMERIC"]
