"""Read-only MetaTrader 5 connection manager.

This module deliberately exposes market-data operations only. It contains no
order submission, position modification, or account-trading methods.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class ConnectionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    INITIALIZING = "INITIALIZING"
    CONNECTED = "CONNECTED"
    AUTHENTICATED = "AUTHENTICATED"
    ERROR = "ERROR"


TIMEFRAME_NAMES = ("5m", "15m", "1h", "4h")


@dataclass(frozen=True)
class ConnectionIdentity:
    broker: str
    server: str
    environment: str
    platform: str = "MT5"


class MT5Connector:
    """Manage an injected MT5 API adapter and enforce read-only data access."""

    def __init__(
        self,
        adapter: Any,
        identity: ConnectionIdentity,
        *,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
        terminal_path: Optional[str] = None,
        reconnect_attempts: int = 1,
    ) -> None:
        self.adapter = adapter
        self.identity = identity
        self.login_id = login
        self.password = password
        self.login_server = server
        self.terminal_path = terminal_path
        self.reconnect_attempts = max(0, int(reconnect_attempts))
        self.state = ConnectionState.DISCONNECTED
        self.last_error = None
        self._timeframes = self._build_timeframes(adapter)

    @staticmethod
    def _build_timeframes(adapter: Any) -> dict:
        if adapter is None:
            return {}
        return {
            "5m": getattr(adapter, "TIMEFRAME_M5", None),
            "15m": getattr(adapter, "TIMEFRAME_M15", None),
            "1h": getattr(adapter, "TIMEFRAME_H1", None),
            "4h": getattr(adapter, "TIMEFRAME_H4", None),
        }

    def _fail(self, message: str) -> bool:
        self.last_error = str(message)
        self.state = ConnectionState.ERROR
        return False

    def _identity_matches(self, account: Any) -> bool:
        company = str(getattr(account, "company", "") or "").strip()
        actual_server = str(getattr(account, "server", "") or "").strip()
        if not company or not actual_server:
            self.last_error = "broker company/server identity is unavailable"
            return False
        expected_broker = self.identity.broker.casefold()
        if expected_broker not in company.casefold() and expected_broker not in actual_server.casefold():
            self.last_error = "connected broker does not match configured broker identity"
            return False
        if self.identity.server and self.identity.server.casefold() != actual_server.casefold():
            self.last_error = "connected server does not match configured server identity"
            return False
        actual_login = getattr(account, "login", None)
        if self.login_id is not None:
            if actual_login is None or int(actual_login) != int(self.login_id):
                self.last_error = "connected account does not match configured login identity"
                return False

        # Never trust the configured environment label by itself. MT5 exposes
        # the actual account trade mode in account_info().trade_mode:
        # DEMO=0, CONTEST=1, REAL=2.
        mode_constants = {
            "demo": getattr(self.adapter, "ACCOUNT_TRADE_MODE_DEMO", None),
            "contest": getattr(self.adapter, "ACCOUNT_TRADE_MODE_CONTEST", None),
            "live": getattr(self.adapter, "ACCOUNT_TRADE_MODE_REAL", None),
        }
        expected_mode = mode_constants.get(self.identity.environment)
        actual_mode = getattr(account, "trade_mode", None)
        if expected_mode is None or actual_mode is None:
            self.last_error = "actual MT5 account trade mode is unavailable"
            return False
        if actual_mode != expected_mode:
            self.last_error = "connected account environment does not match configured environment"
            return False
        return True

    def connect(self) -> bool:
        self.state = ConnectionState.INITIALIZING
        self.last_error = None
        if self.adapter is None:
            return self._fail("MetaTrader5 adapter is unavailable")
        if self.identity.environment not in {"demo", "live", "contest"}:
            return self._fail("account environment must be demo, live, or contest")
        if not self.identity.broker.strip():
            return self._fail("configured broker identity is required")

        kwargs = {}
        if self.terminal_path:
            kwargs["path"] = self.terminal_path
        try:
            if not self.adapter.initialize(**kwargs):
                return self._fail("MT5 terminal initialization failed")
            if self.login_id or self.password or self.login_server:
                if not (self.login_id and self.password and self.login_server):
                    return self._fail(
                        "MT5 login, password and server must be configured together"
                    )
                if not self.adapter.login(
                    login=self.login_id,
                    password=self.password,
                    server=self.login_server,
                ):
                    return self._fail("MT5 account authentication failed")

            terminal = self.adapter.terminal_info()
            account = self.adapter.account_info()
            if terminal is None or account is None or not bool(
                getattr(terminal, "connected", False)
            ):
                return self._fail("terminal is not connected to an authenticated account")

            if not self._identity_matches(account):
                return self._fail(self.last_error or "connected account identity rejected")

            self.state = ConnectionState.AUTHENTICATED
            return True
        except Exception as exc:
            return self._fail(f"MT5 connection error: {type(exc).__name__}")

    def is_connected(self) -> bool:
        # A live terminal alone is insufficient: identity verification must
        # have succeeded during connect() before the session is trusted.
        if self.adapter is None or self.state != ConnectionState.AUTHENTICATED:
            return False
        try:
            terminal = self.adapter.terminal_info()
            account = self.adapter.account_info()
            connected = bool(
                terminal is not None
                and account is not None
                and getattr(terminal, "connected", False)
                and self._identity_matches(account)
            )
            if not connected:
                self.state = ConnectionState.DISCONNECTED
            return connected
        except Exception:
            self.state = ConnectionState.DISCONNECTED
            return False

    def ensure_connected(self) -> bool:
        if self.is_connected():
            return True
        for _ in range(self.reconnect_attempts):
            if self.connect():
                return True
        return False

    def discover_symbols(self, *, selected_only: bool = False) -> list[dict]:
        if not self.ensure_connected():
            raise RuntimeError("MT5 broker session is disconnected")
        symbols = self.adapter.symbols_get()
        if symbols is None:
            raise RuntimeError("MT5 symbol discovery failed")
        discovered = []
        for item in symbols:
            name = str(getattr(item, "name", "") or "").strip()
            if not name:
                continue
            selected = bool(getattr(item, "select", False))
            visible = bool(getattr(item, "visible", False))
            if selected_only and not selected:
                continue
            discovered.append({
                "name": name,
                "description": str(getattr(item, "description", "") or ""),
                "selected": selected,
                "visible": visible,
            })
        return sorted(discovered, key=lambda row: row["name"].casefold())

    def select_symbol(self, exact_symbol: str) -> dict:
        if not exact_symbol or exact_symbol.strip() != exact_symbol:
            raise ValueError("an exact broker symbol is required")
        if not self.ensure_connected():
            raise RuntimeError("MT5 broker session is disconnected")
        info = self.adapter.symbol_info(exact_symbol)
        if info is None:
            raise LookupError("exact broker symbol was not found")
        if not self.adapter.symbol_select(exact_symbol, True):
            raise RuntimeError("MT5 could not select the exact broker symbol")
        refreshed = self.adapter.symbol_info(exact_symbol)
        if refreshed is None:
            raise RuntimeError("selected broker symbol is no longer available")
        return {
            "name": exact_symbol,
            "description": str(getattr(refreshed, "description", "") or ""),
            "selected": bool(getattr(refreshed, "select", True)),
            "visible": bool(getattr(refreshed, "visible", True)),
        }

    def fetch_rates(
        self, exact_symbol: str, timeframe: str, count: int, start_pos: int = 0
    ):
        if timeframe not in self._timeframes or self._timeframes[timeframe] is None:
            raise ValueError("unsupported MT5 timeframe")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1 or count > 2000:
            raise ValueError("count must be an integer between 1 and 2000")
        if not isinstance(start_pos, int) or isinstance(start_pos, bool) or start_pos < 0 or start_pos > 1000000:
            raise ValueError("start_pos must be an integer between 0 and 1000000")
        self.select_symbol(exact_symbol)
        rates = self.adapter.copy_rates_from_pos(
            exact_symbol, self._timeframes[timeframe], start_pos, count
        )
        if rates is None or len(rates) == 0:
            error = self.adapter.last_error() if hasattr(self.adapter, "last_error") else "no rates"
            raise RuntimeError(f"broker returned no rates: {error}")
        return rates

    def close(self) -> None:
        if self.adapter is not None:
            try:
                self.adapter.shutdown()
            finally:
                self.state = ConnectionState.DISCONNECTED
