from types import SimpleNamespace
from threading import Thread
import time

import pytest

from core.mt5_connector import (
    ConnectionIdentity,
    ConnectionState,
    MT5Connector,
)


class FakeMT5:
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_H1 = 60
    TIMEFRAME_H4 = 240
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_CONTEST = 1
    ACCOUNT_TRADE_MODE_REAL = 2

    def __init__(self):
        self.initialized = False
        self.connected = True
        self.login_calls = []
        self.selected = []
        self.rate_calls = []
        self.rates = [{"time": 1}]
        self.symbols = [
            SimpleNamespace(name="SFX Vol 75", description="SFX Vol 75", select=True, visible=True),
            SimpleNamespace(name="EURUSD", description="Euro vs US Dollar", select=False, visible=False),
        ]

    def initialize(self, **kwargs):
        self.initialized = True
        self.connected = True
        return True

    def login(self, **kwargs):
        self.login_calls.append(kwargs)
        return True

    def terminal_info(self):
        return SimpleNamespace(connected=self.connected) if self.initialized else None

    def account_info(self):
        if not self.initialized or not self.connected:
            return None
        return SimpleNamespace(company="Weltrade Ltd", server="Weltrade-Demo", login=123456, trade_mode=0)

    def symbols_get(self):
        return self.symbols

    def symbol_info(self, name):
        for item in self.symbols:
            if item.name == name:
                return item
        return None

    def symbol_select(self, name, enabled):
        self.selected.append((name, enabled))
        return self.symbol_info(name) is not None

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        self.rate_calls.append((symbol, timeframe, start, count))
        return self.rates

    def last_error(self):
        return (0, "ok")

    def shutdown(self):
        self.initialized = False


TEST_FINGERPRINT_KEY = "unit-test-account-fingerprint-key-32-bytes-minimum"


def make_connector(adapter=None, **kwargs):
    adapter = adapter or FakeMT5()
    kwargs.setdefault("fingerprint_key", TEST_FINGERPRINT_KEY)
    connector = MT5Connector(
        adapter,
        ConnectionIdentity("Weltrade", "Weltrade-Demo", "demo"),
        **kwargs,
    )
    return connector, adapter


def test_account_fingerprint_is_keyed_and_stable_for_the_same_account():
    adapter = FakeMT5()
    connector, _ = make_connector(adapter)
    account = SimpleNamespace(
        company="Weltrade Ltd", server="Weltrade-Demo",
        login=123456, trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO,
    )
    fingerprint = connector._fingerprint_for_account(account)
    assert len(fingerprint) == 64
    assert fingerprint == connector._fingerprint_for_account(account)

    other_key = MT5Connector(
        adapter,
        ConnectionIdentity("Weltrade", "Weltrade-Demo", "demo"),
        fingerprint_key="a-different-fingerprint-key-32-bytes",
    )
    assert other_key._fingerprint_for_account(account) != fingerprint


def test_connector_requires_a_strong_fingerprint_key():
    with pytest.raises(ValueError, match="at least 32"):
        MT5Connector(
            FakeMT5(),
            ConnectionIdentity("Weltrade", "Weltrade-Demo", "demo"),
            fingerprint_key="too-short",
        )


def test_connector_rejects_invalid_expected_fingerprint():
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        make_connector(expected_account_fingerprint="not-a-digest")


def test_expected_account_pin_is_checked_on_initial_authentication_and_restart():
    original_account = SimpleNamespace(
        company="Weltrade Ltd", server="Weltrade-Demo",
        login=123456, trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO,
    )
    probe, _ = make_connector()
    expected = probe._fingerprint_for_account(original_account)

    same_account_connector, _ = make_connector(
        expected_account_fingerprint=expected
    )
    assert same_account_connector.connect() is True
    identity = same_account_connector.get_account_identity()
    assert identity["identity_pinned"] is True
    assert identity["account_fingerprint"] == expected
    assert identity["account_login_suffix"] == "3456"

    switched_adapter = FakeMT5()
    switched_adapter.account_info = lambda: SimpleNamespace(
        company="Weltrade Ltd", server="Weltrade-Demo",
        login=654321, trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO,
    )
    restarted_connector, _ = make_connector(
        switched_adapter,
        expected_account_fingerprint=expected,
    )
    assert restarted_connector.connect() is False
    assert "pinned account fingerprint" in restarted_connector.last_error


def test_connector_requires_exact_configured_server():
    connector = MT5Connector(
        FakeMT5(),
        ConnectionIdentity("Weltrade", "", "demo"),
        fingerprint_key=TEST_FINGERPRINT_KEY,
    )
    assert connector.connect() is False
    assert "exact configured broker server" in connector.last_error


def test_connect_authenticates_and_verifies_broker_server():
    connector, adapter = make_connector(
        login=123456,
        password="never-log-this",
        server="Weltrade-Demo",
    )
    assert connector.connect() is True
    assert connector.state is ConnectionState.AUTHENTICATED
    identity = connector.get_account_identity()
    assert identity["identity_pinned"] is False
    assert identity["account_login_suffix"] == "3456"
    assert "login" not in identity
    assert adapter.login_calls[0]["password"] == "never-log-this"
    assert "never-log-this" not in str(connector.last_error)


def test_connect_rejects_wrong_broker_identity():
    adapter = FakeMT5()
    adapter.account_info = lambda: SimpleNamespace(
        company="Other Broker", server="Other-Demo"
    )
    connector, _ = make_connector(adapter)
    assert connector.connect() is False
    assert connector.state is ConnectionState.ERROR
    assert "does not match" in connector.last_error


def test_connect_requires_complete_explicit_credentials():
    connector, adapter = make_connector(login=1234, password="secret")
    assert connector.connect() is False
    assert "must be configured together" in connector.last_error
    assert adapter.initialized is False


def test_symbol_discovery_does_not_assume_family_identity():
    connector, _ = make_connector()
    assert connector.connect()
    rows = connector.discover_symbols()
    assert [row["name"] for row in rows] == ["EURUSD", "SFX Vol 75"]
    assert rows[0]["selected"] is False


def test_exact_symbol_selection_only():
    connector, adapter = make_connector()
    assert connector.connect()
    selected = connector.select_symbol("SFX Vol 75")
    assert selected["name"] == "SFX Vol 75"
    assert adapter.selected == [("SFX Vol 75", True)]
    with pytest.raises(LookupError):
        connector.select_symbol("SFX_VOL_75")


def test_unsupported_timeframe_and_invalid_count_are_rejected():
    connector, _ = make_connector()
    assert connector.connect()
    with pytest.raises(ValueError, match="unsupported"):
        connector.fetch_rates("EURUSD", "3m", 100)
    with pytest.raises(ValueError, match="count"):
        connector.fetch_rates("EURUSD", "5m", 20001)


def test_empty_history_is_reported_not_silently_replaced():
    connector, adapter = make_connector()
    assert connector.connect()
    adapter.rates = []
    with pytest.raises(RuntimeError, match="no rates"):
        connector.fetch_rates("EURUSD", "5m", 100)


def test_reconnect_attempt_restores_connection():
    connector, adapter = make_connector(reconnect_attempts=1)
    assert connector.connect()
    adapter.connected = False
    assert connector.ensure_connected() is True
    assert connector.state is ConnectionState.AUTHENTICATED


def test_connector_exposes_no_trade_execution_methods():
    connector, _ = make_connector()
    assert not hasattr(connector, "order_send")
    assert not hasattr(connector, "positions_get")
    assert not hasattr(connector, "order_modify")


def test_failed_identity_verification_cannot_be_bypassed_by_live_terminal():
    adapter = FakeMT5()
    adapter.account_info = lambda: SimpleNamespace(
        company="Wrong Broker", server="Wrong-Demo"
    )
    connector, _ = make_connector(adapter)
    assert connector.connect() is False
    # The terminal reports connected, but its broker identity was rejected.
    assert connector.is_connected() is False
    assert connector.ensure_connected() is False


def test_connected_account_switch_is_detected():
    connector, adapter = make_connector()
    assert connector.connect()
    adapter.account_info = lambda: SimpleNamespace(
        company="Different Broker", server="Different-Server"
    )
    assert connector.is_connected() is False
    assert connector.state is ConnectionState.DISCONNECTED


def test_history_pagination_uses_requested_start_position():
    connector, adapter = make_connector()
    assert connector.connect()
    connector.fetch_rates("EURUSD", "1h", 500, start_pos=2000)
    assert adapter.rate_calls == [("EURUSD", FakeMT5.TIMEFRAME_H1, 2000, 500)]


def test_history_pagination_rejects_out_of_range_start_position():
    connector, _ = make_connector()
    assert connector.connect()
    with pytest.raises(ValueError, match="start_pos"):
        connector.fetch_rates("EURUSD", "1h", 100, start_pos=1000001)
    with pytest.raises(ValueError, match="start_pos"):
        connector.fetch_rates("EURUSD", "1h", 2, start_pos=999999)


def test_demo_configuration_rejects_actual_live_account():
    connector, adapter = make_connector()
    adapter.account_info = lambda: SimpleNamespace(
        company="Weltrade Ltd", server="Weltrade-Demo",
        login=123456, trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_REAL,
    )
    assert connector.connect() is False
    assert "environment does not match" in connector.last_error


def test_live_and_contest_modes_are_verified_against_mt5_constants():
    live, live_adapter = make_connector()
    live.identity = ConnectionIdentity("Weltrade", "Weltrade-Live", "live")
    live_adapter.account_info = lambda: SimpleNamespace(
        company="Weltrade Ltd", server="Weltrade-Live",
        login=123456, trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_REAL,
    )
    assert live.connect() is True

    contest, contest_adapter = make_connector()
    contest.identity = ConnectionIdentity("Weltrade", "Weltrade-Contest", "contest")
    contest_adapter.account_info = lambda: SimpleNamespace(
        company="Weltrade Ltd", server="Weltrade-Contest",
        login=123456, trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_CONTEST,
    )
    assert contest.connect() is True


def test_missing_account_trade_mode_fails_closed():
    connector, adapter = make_connector()
    adapter.account_info = lambda: SimpleNamespace(
        company="Weltrade Ltd", server="Weltrade-Demo", login=123456
    )
    assert connector.connect() is False
    assert "trade mode is unavailable" in connector.last_error


def test_mt5_api_calls_are_serialized_across_threads():
    connector, adapter = make_connector()
    assert connector.connect()
    active = 0
    maximum_active = 0

    def concurrent_copy(symbol, timeframe, start, count):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        time.sleep(0.02)
        active -= 1
        return [{"time": start + 1}]

    adapter.copy_rates_from_pos = concurrent_copy
    threads = [
        Thread(target=connector.fetch_rates, args=("EURUSD", "5m", 10)),
        Thread(target=connector.fetch_rates, args=("EURUSD", "5m", 10)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert maximum_active == 1


def test_failed_broker_identity_shuts_down_mt5_api_session():
    adapter = FakeMT5()
    adapter.account_info = lambda: SimpleNamespace(
        company="Wrong Broker", server="Wrong-Demo",
        login=123456, trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO,
    )
    connector, _ = make_connector(adapter)
    assert connector.connect() is False
    assert adapter.initialized is False


def test_account_fingerprint_is_stable_opaque_and_bound_to_login():
    connector, _ = make_connector()
    assert connector.connect()
    identity = connector.get_account_identity()
    assert len(identity["account_fingerprint"]) == 64
    assert "login" not in identity
    assert "123456" not in str(identity)
    assert identity["server"] == "Weltrade-Demo"
    assert identity["environment"] == "demo"


def test_same_server_account_switch_is_detected_by_fingerprint():
    connector, adapter = make_connector()
    assert connector.connect()
    adapter.account_info = lambda: SimpleNamespace(
        company="Weltrade Ltd", server="Weltrade-Demo",
        login=999999, trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO,
    )
    assert connector.is_connected() is False
    assert connector.state is ConnectionState.DISCONNECTED


def test_symbol_discovery_includes_contract_metadata():
    connector, adapter = make_connector()
    adapter.symbols = [SimpleNamespace(
        name="SFX Vol 75", description="SFX Vol 75", select=True,
        visible=True, path="Synthetics\\SFX", digits=2, point=0.01,
        trade_mode=4, trade_contract_size=1.0, trade_tick_size=0.01,
        trade_tick_value=0.01, currency_base="", currency_profit="USD",
        currency_margin="USD", volume_min=0.01, volume_max=100.0,
        volume_step=0.01,
    )]
    assert connector.connect()
    symbol = connector.discover_symbols()[0]
    assert symbol["metadata"]["digits"] == 2
    assert symbol["metadata"]["trade_tick_size"] == 0.01
    assert symbol["metadata"]["path"] == "Synthetics\\SFX"


def test_reconnect_refuses_silent_switch_to_another_account_on_same_server():
    connector, adapter = make_connector(reconnect_attempts=1)
    assert connector.connect()
    adapter.account_info = lambda: SimpleNamespace(
        company="Weltrade Ltd", server="Weltrade-Demo",
        login=999999, trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO,
    )
    adapter.connected = False
    assert connector.ensure_connected() is False
    assert connector.state is ConnectionState.ERROR
    assert "fingerprint differs" in connector.last_error


class _FakeStructuredRates:
    dtype = SimpleNamespace(names=(
        "time", "open", "high", "low", "close",
        "tick_volume", "real_volume", "spread",
    ))

    def __init__(self, times):
        self.rows = [
            (timestamp, 1.0, 1.2, 0.9, 1.1, 10, 0, 1)
            for timestamp in times
        ]

    def __len__(self):
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)

    def __getitem__(self, key):
        if key == "time":
            return [row[0] for row in self.rows]
        return self.rows[key]


def _structured_rate_array(times):
    return _FakeStructuredRates(times)


def test_paginated_history_is_reassembled_in_chronological_order():
    connector, adapter = make_connector()
    page_newer = _structured_rate_array(range(2001, 4001))
    page_older = _structured_rate_array(range(1001, 2001))
    latest = _structured_rate_array([4000])

    def paged_copy(symbol, timeframe, start, count):
        adapter.rate_calls.append((symbol, timeframe, start, count))
        if start == 0 and count == 1:
            return latest
        if start == 0:
            return page_newer
        if start == 2000:
            return page_older
        return _structured_rate_array([])

    adapter.copy_rates_from_pos = paged_copy
    assert connector.connect()
    rates, metadata = connector.fetch_rates_detailed("EURUSD", "5m", 3000)

    assert len(rates) == 3000
    assert int(rates["time"][0]) == 1001
    assert int(rates["time"][-1]) == 4000
    assert metadata["requested_count"] == 3000
    assert metadata["returned_count"] == 3000
    assert metadata["page_count"] == 2
    assert metadata["page_sizes"] == [2000, 1000]
    assert metadata["page_start_positions"] == [0, 2000]
    assert metadata["snapshot_check_performed"] is True
    assert metadata["snapshot_stable"] is True


def test_paginated_history_marks_snapshot_drift():
    connector, adapter = make_connector()
    page_newer = _structured_rate_array(range(2001, 4001))
    page_older = _structured_rate_array(range(1001, 2001))
    latest_values = iter([4000, 4300])

    def drifting_copy(symbol, timeframe, start, count):
        adapter.rate_calls.append((symbol, timeframe, start, count))
        if start == 0 and count == 1:
            return _structured_rate_array([next(latest_values)])
        if start == 0:
            return page_newer
        if start == 2000:
            return page_older
        return _structured_rate_array([])

    adapter.copy_rates_from_pos = drifting_copy
    assert connector.connect()
    _, metadata = connector.fetch_rates_detailed("EURUSD", "5m", 3000)
    assert metadata["snapshot_stable"] is False
    assert metadata["latest_bar_time_before"] == 4000
    assert metadata["latest_bar_time_after"] == 4300


def test_partial_paginated_history_reports_returned_count():
    connector, adapter = make_connector()
    partial_page = _structured_rate_array(range(1001, 2001))
    latest = _structured_rate_array([2000])

    def partial_copy(symbol, timeframe, start, count):
        adapter.rate_calls.append((symbol, timeframe, start, count))
        if start == 0 and count == 1:
            return latest
        if start == 0:
            return partial_page
        return _structured_rate_array([])

    adapter.copy_rates_from_pos = partial_copy
    assert connector.connect()
    _, metadata = connector.fetch_rates_detailed("EURUSD", "5m", 3000)
    assert metadata["requested_count"] == 3000
    assert metadata["returned_count"] == 1000
    assert metadata["page_count"] == 1
    assert metadata["snapshot_stable"] is True


def test_numpy_structured_pages_use_native_concatenation_when_available():
    np = pytest.importorskip("numpy")
    connector, adapter = make_connector()
    dtype = [
        ("time", "i8"), ("open", "f8"), ("high", "f8"),
        ("low", "f8"), ("close", "f8"), ("tick_volume", "i8"),
        ("real_volume", "i8"), ("spread", "i8"),
    ]

    def native_rates(times):
        return np.array([
            (timestamp, 1.0, 1.2, 0.9, 1.1, 10, 0, 1)
            for timestamp in times
        ], dtype=dtype)

    newer = native_rates(range(2001, 4001))
    older = native_rates(range(1001, 2001))
    latest = native_rates([4000])

    def paged_copy(symbol, timeframe, start, count):
        if start == 0 and count == 1:
            return latest
        if start == 0:
            return newer
        if start == 2000:
            return older
        return native_rates([])

    adapter.copy_rates_from_pos = paged_copy
    assert connector.connect()
    rates, metadata = connector.fetch_rates_detailed("EURUSD", "5m", 3000)

    assert isinstance(rates, np.ndarray)
    assert int(rates["time"][0]) == 1001
    assert int(rates["time"][-1]) == 4000
    assert metadata["snapshot_stable"] is True
