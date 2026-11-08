from types import SimpleNamespace

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


def make_connector(adapter=None, **kwargs):
    adapter = adapter or FakeMT5()
    connector = MT5Connector(
        adapter,
        ConnectionIdentity("Weltrade", "Weltrade-Demo", "demo"),
        **kwargs,
    )
    return connector, adapter


def test_connect_authenticates_and_verifies_broker_server():
    connector, adapter = make_connector(
        login=123456,
        password="never-log-this",
        server="Weltrade-Demo",
    )
    assert connector.connect() is True
    assert connector.state is ConnectionState.AUTHENTICATED
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
    connector, _ = make_connector(login=1234, password="secret")
    assert connector.connect() is False
    assert "must be configured together" in connector.last_error


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
        connector.fetch_rates("EURUSD", "5m", 2001)


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
