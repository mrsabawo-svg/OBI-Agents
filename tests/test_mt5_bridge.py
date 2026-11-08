from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import mt5_bridge


def test_bridge_rejects_unsupported_timeframe():
    client = mt5_bridge.app.test_client()
    response = client.get("/ohlcv?symbol=SFX_VOL_75&timeframe=3m")
    assert response.status_code == 400
    assert "unsupported timeframe" in response.get_json()["error"]


def test_bridge_rejects_unbounded_count():
    client = mt5_bridge.app.test_client()
    response = client.get("/ohlcv?symbol=SFX_VOL_75&timeframe=5m&count=20001")
    assert response.status_code == 400
    assert "between 1 and 20000" in response.get_json()["error"]


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


def test_bar_status_uses_history_completeness_forming_indexes():
    assert mt5_bridge._bar_status(0, set()) == "closed"
    assert mt5_bridge._bar_status(1, {1}) == "forming"
    assert mt5_bridge._bar_status(2, {1}) == "closed"


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
        row, 0, set(), ["INVALID_TIMESTAMP", "INVALID_NUMERIC"], "raw-observation-001"
    )
    assert response["observation_id"] == "raw-observation-001"
    assert response["timestamp"] is None
    assert response["Open"] is None
    assert response["bar_status"] == "closed"
    assert response["quarantine_reasons"] == ["INVALID_TIMESTAMP", "INVALID_NUMERIC"]


def test_ohlcv_endpoint_persists_raw_rows_and_quarantine_links_atomically(monkeypatch, tmp_path):
    frozen_now = datetime.now(timezone.utc)
    current_bucket = int(frozen_now.timestamp() // 300) * 300

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now

    class FakeRates:
        dtype = SimpleNamespace(names=(
            "time", "open", "high", "low", "close",
            "tick_volume", "real_volume", "spread",
        ))

        def __iter__(self):
            return iter([
                (current_bucket - 300, 10.0, 12.0, 9.0, 11.0, 10, 0, 1),
                (current_bucket, 10.0, 8.0, 9.0, 11.0, 10, 0, 1),
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
                "identity_pinned": True,
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

        def fetch_rates_detailed(self, symbol, timeframe, count, start_pos):
            assert (symbol, timeframe, count, start_pos) == ("SFX Vol 75", "5m", 2, 0)
            return FakeRates(), {
                "requested_count": count,
                "returned_count": 2,
                "page_count": 1,
                "page_sizes": [2],
                "page_start_positions": [start_pos],
                "snapshot_check_performed": False,
                "snapshot_stable": True,
            }

        def is_connected(self):
            return True

    class FakeMT5:
        def account_info(self):
            return SimpleNamespace(server="Weltrade-Demo")

        def symbol_info(self, symbol):
            return SimpleNamespace(description="SFX Vol 75")

    monkeypatch.setattr(mt5_bridge, "datetime", FrozenDateTime)
    monkeypatch.setattr(
        mt5_bridge, "MT5_VERIFIED_SESSION_MODELS",
        {"SFX_VOL_75": "continuous_24_7"},
    )
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
    first_response = client.get("/ohlcv?symbol=SFX_VOL_75&timeframe=5m&count=2")
    first_payload = first_response.get_json()

    assert first_response.status_code == 200
    assert first_payload["instrument_verification_status"] == "partially_verified"
    assert first_payload["timeframe_verified"] is False
    assert first_payload["analysis_eligible"] is False
    invalid_verification = client.post("/instruments/verify", json={
        "instrument_id": "SFX_VOL_75",
        "broker_symbol": "SFX Vol 75",
        "reviewer": "test-operator",
        "evidence_report_ids": ["not-a-persisted-report"],
    })
    assert invalid_verification.status_code == 422

    verification_response = client.post("/instruments/verify", json={
        "instrument_id": "SFX_VOL_75",
        "broker_symbol": "SFX Vol 75",
        "reviewer": "test-operator",
        "evidence_report_ids": [first_payload["quality_report"]["report_id"]],
    })
    verification_payload = verification_response.get_json()
    assert verification_response.status_code == 201
    assert verification_payload["instrument_verification_status"] == "verified"
    assert verification_payload["analysis_eligible_timeframes"] == ["5m"]

    response = client.get("/ohlcv?symbol=SFX_VOL_75&timeframe=5m&count=2")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["instrument_verification_status"] == "verified"
    assert payload["instrument_verification_id"] == verification_payload["verification"]["verification_id"]
    assert payload["timeframe_verified"] is True
    assert payload["analysis_eligible"] is True
    assert payload["account_identity"]["account_fingerprint"] == "e" * 64
    assert payload["history_completeness"]["status"] == "complete"
    assert payload["history_completeness"]["session_model"] == "continuous_24_7"
    assert payload["history_completeness"]["requested_count"] == 2
    assert payload["history_completeness"]["returned_count"] == 2
    assert payload["history_completeness"]["forming_count"] == 1
    assert payload["data"][1]["bar_status"] == "forming"
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
    # Both /ohlcv calls are distinct broker retrievals. The raw evidence store
    # is append-only, so each malformed observation must retain its own
    # quarantine link rather than being silently deduplicated.
    assert len(quarantine) == 2
    assert {record["observation_id"] for record in quarantine} == {
        first_payload["data"][1]["observation_id"],
        payload["data"][1]["observation_id"],
    }
    assert {record["reason_code"] for record in quarantine} == {"INVALID_OHLC"}

    from core.canonical_data_store import CanonicalCandleStore
    canonical = CanonicalCandleStore(tmp_path / "raw.sqlite3")
    # Both requests persist a separate canonical record because each has its
    # own raw observation and validation report. Canonical lineage is append-only.
    assert canonical.count() == 2
    first_saved = canonical.get(first_payload["canonical_persistence"]["candle_ids"][0])
    saved = canonical.get(payload["canonical_persistence"]["candle_ids"][0])
    assert first_saved["source_observation_ids"] == [first_payload["data"][0]["observation_id"]]
    assert saved["source_observation_ids"] == [payload["data"][0]["observation_id"]]
    assert first_saved["candle_id"] != saved["candle_id"]
    assert first_saved["validation_report_id"] == first_payload["quality_report"]["report_id"]
    assert saved["validation_report_id"] == payload["quality_report"]["report_id"]


def test_ohlcv_requires_account_scoped_symbol_discovery(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import mt5_bridge

    class FakeConnector:
        def get_account_identity(self):
            return {
                "account_fingerprint": "f" * 64,
                "identity_pinned": True,
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


def test_ohlcv_blocks_metadata_drift_after_symbol_discovery(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import mt5_bridge

    metadata = {"name": "SFX Vol 75", "digits": 2, "point": 0.01}

    class FakeConnector:
        def get_account_identity(self):
            return {
                "account_fingerprint": "1" * 64,
                "identity_pinned": True,
                "broker_company": "Weltrade Ltd",
                "server": "Weltrade-Demo",
                "environment": "demo",
                "platform": "MT5",
            }

        def discover_symbols(self):
            return [{
                "name": "SFX Vol 75", "description": "SFX Vol 75",
                "selected": True, "visible": True, "metadata": metadata,
            }]

        def select_symbol(self, symbol):
            return {
                "name": symbol, "description": symbol,
                "metadata": {"name": "SFX Vol 75", "digits": 3, "point": 0.001},
            }

    monkeypatch.setattr(mt5_bridge, "connector", FakeConnector())
    monkeypatch.setattr(mt5_bridge, "SYMBOL_MAP", {"SFX_VOL_75": "SFX Vol 75"})
    monkeypatch.setattr(mt5_bridge, "MT5_RAW_DB_PATH", str(tmp_path / "registry.sqlite3"))
    monkeypatch.setattr(mt5_bridge, "MT5_BRIDGE_HOST", "127.0.0.1")
    monkeypatch.setattr(mt5_bridge, "MT5_BRIDGE_TOKEN", "")
    client = mt5_bridge.app.test_client()
    assert client.get("/symbols").status_code == 200

    response = client.get("/ohlcv?symbol=SFX_VOL_75&timeframe=5m&count=2")
    assert response.status_code == 409
    assert "metadata changed" in response.get_json()["error"]


def test_ohlcv_refuses_unpinned_account_even_after_symbol_discovery(monkeypatch, tmp_path):
    class FakeConnector:
        def get_account_identity(self):
            return {
                "account_fingerprint": "2" * 64,
                "identity_pinned": False,
                "broker_company": "Weltrade Ltd",
                "server": "Weltrade-Demo",
                "environment": "demo",
                "platform": "MT5",
            }

        def discover_symbols(self):
            return [{
                "name": "SFX Vol 75",
                "description": "SFX Vol 75",
                "selected": True,
                "visible": True,
                "metadata": {"name": "SFX Vol 75"},
            }]

    monkeypatch.setattr(mt5_bridge, "connector", FakeConnector())
    monkeypatch.setattr(mt5_bridge, "SYMBOL_MAP", {"SFX_VOL_75": "SFX Vol 75"})
    monkeypatch.setattr(mt5_bridge, "MT5_RAW_DB_PATH", str(tmp_path / "registry.sqlite3"))
    monkeypatch.setattr(mt5_bridge, "MT5_BRIDGE_HOST", "127.0.0.1")
    monkeypatch.setattr(mt5_bridge, "MT5_BRIDGE_TOKEN", "")
    client = mt5_bridge.app.test_client()
    discovery = client.get("/symbols")
    assert discovery.status_code == 200
    assert discovery.get_json()["account_identity_pinned"] is False

    response = client.get("/ohlcv?symbol=SFX_VOL_75&timeframe=5m&count=2")
    assert response.status_code == 409
    assert "identity is not pinned" in response.get_json()["error"]


def test_init_mt5_requires_stable_fingerprint_key_and_exact_server(monkeypatch):
    monkeypatch.setattr(mt5_bridge, "mt5", object())
    monkeypatch.setattr(mt5_bridge, "MT5_BROKER_NAME", "Weltrade")
    monkeypatch.setattr(mt5_bridge, "MT5_ACCOUNT_ENVIRONMENT", "demo")
    monkeypatch.setattr(mt5_bridge, "MT5_SERVER", "")
    monkeypatch.setattr(mt5_bridge, "MT5_ACCOUNT_FINGERPRINT_KEY", "k" * 40)
    ok, message = mt5_bridge.init_mt5()
    assert ok is False
    assert "exact expected broker server" in message

    monkeypatch.setattr(mt5_bridge, "MT5_SERVER", "Weltrade-Demo")
    monkeypatch.setattr(mt5_bridge, "MT5_ACCOUNT_FINGERPRINT_KEY", "short")
    ok, message = mt5_bridge.init_mt5()
    assert ok is False
    assert "fingerprint_key" in message.lower()


def test_init_mt5_clears_stale_connector_before_rejecting_configuration(monkeypatch):
    stale_connector = object()
    monkeypatch.setattr(mt5_bridge, "connector", stale_connector)
    monkeypatch.setattr(mt5_bridge, "mt5", None)

    ok, message = mt5_bridge.init_mt5()

    assert ok is False
    assert "package not installed" in message
    assert mt5_bridge.connector is None


def test_init_mt5_uses_prelogged_terminal_without_triggering_partial_login(monkeypatch):
    captured = {}

    class FakeConnector:
        def __init__(self, adapter, identity, **kwargs):
            captured["identity"] = identity
            captured["kwargs"] = kwargs
            self.last_error = None

        def connect(self):
            return True

    monkeypatch.setattr(mt5_bridge, "mt5", object())
    monkeypatch.setattr(mt5_bridge, "MT5Connector", FakeConnector)
    monkeypatch.setattr(mt5_bridge, "MT5_BROKER_NAME", "Weltrade")
    monkeypatch.setattr(mt5_bridge, "MT5_ACCOUNT_ENVIRONMENT", "demo")
    monkeypatch.setattr(mt5_bridge, "MT5_SERVER", "Weltrade-Demo")
    monkeypatch.setattr(mt5_bridge, "MT5_LOGIN", 0)
    monkeypatch.setattr(mt5_bridge, "MT5_PASSWORD", "")
    monkeypatch.setattr(mt5_bridge, "MT5_PATH", "")
    monkeypatch.setattr(mt5_bridge, "MT5_ACCOUNT_FINGERPRINT_KEY", "k" * 40)
    monkeypatch.setattr(mt5_bridge, "MT5_EXPECTED_ACCOUNT_FINGERPRINT", None)
    monkeypatch.setattr(mt5_bridge, "connector", None)

    ok, message = mt5_bridge.init_mt5()
    assert (ok, message) == (True, "connected")
    assert captured["identity"].server == "Weltrade-Demo"
    assert captured["kwargs"]["server"] is None
    assert captured["kwargs"]["fingerprint_key"] == "k" * 40
    assert captured["kwargs"]["expected_account_fingerprint"] is None


def test_verified_session_model_configuration_is_explicit_and_strict():
    assert mt5_bridge._load_verified_session_models("") == {}
    assert mt5_bridge._load_verified_session_models(
        '{"SFX_VOL_75":"continuous_24_7"}'
    ) == {"SFX_VOL_75": "continuous_24_7"}
    with pytest.raises(ValueError, match="valid JSON"):
        mt5_bridge._load_verified_session_models("{invalid")
    with pytest.raises(ValueError, match="continuous_24_7"):
        mt5_bridge._load_verified_session_models(
            '{"EURUSD":"assumed_24_5"}'
        )
