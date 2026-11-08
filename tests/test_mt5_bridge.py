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
         "tick_volume": -1, "real_volume": 0, "spread": 1},
        {"time": 300, "open": 10, "high": 8, "low": 9, "close": 11,
         "tick_volume": 10, "real_volume": 0, "spread": 1},
        {"time": 100, "open": 10, "high": 12, "low": 9, "close": 11,
         "tick_volume": 10, "real_volume": 0, "spread": 1},
    ]
    reasons = mt5_bridge._quarantine_reasons(rows)
    reason_pairs = {(item["observation_index"], item["reason_code"]) for item in reasons}

    assert (1, "INVALID_TIMESTAMP") in reason_pairs
    assert (2, "INVALID_NUMERIC") in reason_pairs
    assert (2, "INVALID_VOLUME") in reason_pairs
    assert (3, "INVALID_OHLC") in reason_pairs
    assert (0, "DUPLICATE_TIMESTAMP") in reason_pairs
    assert (4, "DUPLICATE_TIMESTAMP") in reason_pairs


def test_response_row_handles_invalid_timestamp_and_non_finite_prices():
    row = {
        "time": float("nan"), "open": float("inf"), "high": 12,
        "low": 9, "close": 11, "tick_volume": 10,
        "real_volume": 0, "spread": 1,
    }
    response = mt5_bridge._response_row(
        row, 0, None, 0, ["INVALID_TIMESTAMP", "INVALID_NUMERIC"], "raw-observation-001"
    )
    assert response["observation_id"] == "raw-observation-001"
    assert response["timestamp"] is None
    assert response["Open"] is None
    assert response["bar_status"] == "closed"
    assert response["quarantine_reasons"] == ["INVALID_TIMESTAMP", "INVALID_NUMERIC"]


def test_ohlcv_endpoint_persists_raw_rows_and_quarantine_links_atomically(monkeypatch, tmp_path):
    from types import SimpleNamespace

    class FakeRates:
        dtype = SimpleNamespace(names=(
            "time", "open", "high", "low", "close",
            "tick_volume", "real_volume", "spread",
        ))

        def __iter__(self):
            return iter([
                (100, 10.0, 12.0, 9.0, 11.0, 10, 0, 1),
                (200, 10.0, 8.0, 9.0, 11.0, 10, 0, 1),
            ])

    class FakeConnector:
        metadata = {
            "name": "SFX Vol 75", "description": "SFX Vol 75",
            "path": "Synthetics\\SFX", "digits": 2, "point": 0.01,
            "trade_mode": 4, "trade_contract_size": 1.0,
            "trade_tick_size": 0.01, "trade_tick_value": 0.01,
            "currency_base": "", "currency_profit": "USD",
            "currency_margin": "USD", "volume_min": 0.01,
            "volume_max": 100.0, "volume_step": 0.01,
            "selected": True, "visible": True,
        }

        def get_account_identity(self):
            return {
                "account_fingerprint": "e" * 64,
                "broker_company": "Weltrade Ltd",
                "server": "Weltrade-Demo",
                "environment": "demo",
                "platform": "MT5",
                "currency": "USD",
                "leverage": 1000,
                "trade_allowed": False,
                "trade_expert": False,
            }

        def discover_symbols(self):
            return [{
                "name": "SFX Vol 75", "description": "SFX Vol 75",
                "selected": True, "visible": True, "metadata": self.metadata,
            }]

        def select_symbol(self, symbol):
            assert symbol == "SFX Vol 75"
            return {"name": symbol, "description": symbol, "metadata": self.metadata}

        def fetch_rates(self, symbol, timeframe, count, start_pos):
            assert (symbol, timeframe, count, start_pos) == ("SFX Vol 75", "5m", 2, 0)
            return FakeRates()

        def is_connected(self):
            return True

    class FakeMT5:
        def account_info(self):
            return SimpleNamespace(server="Weltrade-Demo")

        def symbol_info(self, symbol):
            return SimpleNamespace(description="SFX Vol 75")

    monkeypatch.setattr(mt5_bridge, "connector", FakeConnector())
    monkeypatch.setattr(mt5_bridge, "mt5", FakeMT5())
    monkeypatch.setattr(mt5_bridge, "SYMBOL_MAP", {"SFX_VOL_75": "SFX Vol 75"})
    monkeypatch.setattr(mt5_bridge, "MT5_BROKER_NAME", "Weltrade")
    monkeypatch.setattr(mt5_bridge, "MT5_ACCOUNT_ENVIRONMENT", "demo")
    monkeypatch.setattr(mt5_bridge, "MT5_RAW_DB_PATH", str(tmp_path / "raw.sqlite3"))
    monkeypatch.setattr(mt5_bridge, "MT5_BRIDGE_HOST", "127.0.0.1")
    monkeypatch.setattr(mt5_bridge, "MT5_BRIDGE_TOKEN", "")

    client = mt5_bridge.app.test_client()
    discovery_response = client.get("/symbols")
    assert discovery_response.status_code == 200
    assert discovery_response.get_json()["discovery"]["symbol_count"] == 1
    response = client.get("/ohlcv?symbol=SFX_VOL_75&timeframe=5m&count=2")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["account_identity"]["account_fingerprint"] == "e" * 64
    assert payload["raw_persistence"]["inserted_count"] == 2
    assert payload["raw_persistence"]["quarantine_count"] == 1
    assert payload["quality_report"]["quarantined_observation_count"] == 1
    assert payload["quality_report"]["report_id"] == payload["canonical_persistence"]["validation_report_id"]
    assert payload["canonical_persistence"]["persisted"] is True
    assert payload["canonical_persistence"]["inserted_count"] == 1
    assert payload["canonical_persistence"]["source_lineage"] == [{
        "candle_id": payload["canonical_persistence"]["candle_ids"][0],
        "observation_id": payload["data"][0]["observation_id"],
    }]
    assert payload["data"][0]["observation_id"] == payload["raw_persistence"]["observation_ids"][0]
    assert payload["data"][1]["quarantine_reasons"] == ["INVALID_OHLC"]

    from core.raw_observation_store import RawObservationStore
    store = RawObservationStore(tmp_path / "raw.sqlite3")
    quarantine = store.get_quarantine_records()
    assert len(quarantine) == 1
    assert quarantine[0]["observation_id"] == payload["data"][1]["observation_id"]
    assert quarantine[0]["reason_code"] == "INVALID_OHLC"

    from core.canonical_data_store import CanonicalCandleStore
    canonical = CanonicalCandleStore(tmp_path / "raw.sqlite3")
    assert canonical.count() == 1
    saved = canonical.get(payload["canonical_persistence"]["candle_ids"][0])
    assert saved["source_observation_ids"] == [payload["data"][0]["observation_id"]]
    assert saved["validation_report_id"] == payload["quality_report"]["report_id"]


def test_ohlcv_requires_account_scoped_symbol_discovery(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import mt5_bridge

    class FakeConnector:
        def get_account_identity(self):
            return {
                "account_fingerprint": "f" * 64,
                "broker_company": "Weltrade Ltd",
                "server": "Weltrade-Demo",
                "environment": "demo",
                "platform": "MT5",
            }

        def is_connected(self):
            return True

    monkeypatch.setattr(mt5_bridge, "connector", FakeConnector())
    monkeypatch.setattr(mt5_bridge, "SYMBOL_MAP", {"SFX_VOL_75": "SFX Vol 75"})
    monkeypatch.setattr(mt5_bridge, "MT5_RAW_DB_PATH", str(tmp_path / "registry.sqlite3"))
    monkeypatch.setattr(mt5_bridge, "MT5_BRIDGE_HOST", "127.0.0.1")
    monkeypatch.setattr(mt5_bridge, "MT5_BRIDGE_TOKEN", "")

    response = mt5_bridge.app.test_client().get(
        "/ohlcv?symbol=SFX_VOL_75&timeframe=5m&count=2"
    )
    assert response.status_code == 409
    assert "call /symbols first" in response.get_json()["error"]
