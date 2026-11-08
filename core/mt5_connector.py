"""Read-only MetaTrader 5 connection manager.

This module deliberately exposes market-data operations only. It contains no
order submission, position modification, or account-trading methods.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from enum import Enum
from functools import wraps
from threading import RLock
from typing import Any, Optional



class _CombinedRateBatch:
    """Small structured-array-compatible fallback for paged MT5 test adapters."""

    def __init__(self, pages):
        self.dtype = getattr(pages[0], "dtype", None)
        self._rows = [row for page in pages for row in page]

    def __len__(self):
        return len(self._rows)

    def __iter__(self):
        return iter(self._rows)

    def __getitem__(self, key):
        if isinstance(key, str):
            names = getattr(self.dtype, "names", None) or ()
            if key not in names:
                raise KeyError(key)
            column_index = names.index(key)
            return [row[column_index] for row in self._rows]
        return self._rows[key]


def _serialized(method):
    """Serialize access to the process-wide MT5 terminal connection."""
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class ConnectionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    INITIALIZING = "INITIALIZING"
    CONNECTED = "CONNECTED"
    AUTHENTICATED = "AUTHENTICATED"
    ERROR = "ERROR"


TIMEFRAME_NAMES = ("5m", "15m", "1h", "4h")
MAX_HISTORY_COUNT = 20000
HISTORY_PAGE_SIZE = 2000


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
        self._lock = RLock()
        self.adapter = adapter
        self._initialized = False
        self.identity = identity
        self.login_id = login
        self.password = password
        self.login_server = server
        self.terminal_path = terminal_path
        self.reconnect_attempts = max(0, int(reconnect_attempts))
        self.state = ConnectionState.DISCONNECTED
        self.last_error = None
        self._timeframes = self._build_timeframes(adapter)
        self._authenticated_fingerprint = None

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
        if self.adapter is not None and self._initialized:
            try:
                self.adapter.shutdown()
            except Exception:
                pass
            self._initialized = False
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
        if actual_login is None:
            self.last_error = "connected account login identity is unavailable"
            return False
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

    @staticmethod
    def _fingerprint_for_account(account: Any) -> str:
        """Create a stable opaque account key without storing the account login."""
        identity = {
            "company": str(getattr(account, "company", "") or "").strip().casefold(),
            "server": str(getattr(account, "server", "") or "").strip().casefold(),
            "login": str(getattr(account, "login", "")),
            "trade_mode": int(getattr(account, "trade_mode")),
        }
        if not identity["company"] or not identity["server"] or not identity["login"]:
            raise ValueError("account identity is incomplete")
        canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(("obi-mt5-account-v1:" + canonical).encode("utf-8")).hexdigest()

    @staticmethod
    def _symbol_metadata(info: Any) -> dict:
        """Return JSON-safe contract metadata, retaining unavailable fields as null."""
        fields = (
            "name", "description", "path", "digits", "point", "trade_mode",
            "trade_contract_size", "trade_tick_size", "trade_tick_value",
            "currency_base", "currency_profit", "currency_margin",
            "volume_min", "volume_max", "volume_step",
        )
        metadata = {}
        for field in fields:
            value = getattr(info, field, None)
            if value is None:
                metadata[field] = None
            elif isinstance(value, (str, bool, int)):
                metadata[field] = value
            else:
                try:
                    number = float(value)
                    metadata[field] = number if math.isfinite(number) else None
                except (TypeError, ValueError, OverflowError):
                    metadata[field] = str(value)
        metadata["selected"] = bool(getattr(info, "select", False))
        metadata["visible"] = bool(getattr(info, "visible", False))
        return metadata

    @_serialized
    def get_account_identity(self) -> dict:
        """Return verified broker identity and an opaque, account-specific fingerprint."""
        if self.adapter is None or self.state != ConnectionState.AUTHENTICATED:
            raise RuntimeError("MT5 account is not authenticated")
        account = self.adapter.account_info()
        if account is None or not self._identity_matches(account):
            self.state = ConnectionState.DISCONNECTED
            raise RuntimeError("MT5 account identity verification failed")
        fingerprint = self._fingerprint_for_account(account)
        if self._authenticated_fingerprint and fingerprint != self._authenticated_fingerprint:
            self.state = ConnectionState.DISCONNECTED
            raise RuntimeError("MT5 account fingerprint changed")
        return {
            "account_fingerprint": fingerprint,
            "broker_company": str(getattr(account, "company", "") or "").strip(),
            "server": str(getattr(account, "server", "") or "").strip(),
            "environment": self.identity.environment,
            "platform": self.identity.platform,
            "currency": str(getattr(account, "currency", "") or ""),
            "leverage": getattr(account, "leverage", None),
            "trade_allowed": bool(getattr(account, "trade_allowed", False)),
            "trade_expert": bool(getattr(account, "trade_expert", False)),
        }

    @_serialized
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
            if self._initialized:
                self.adapter.shutdown()
                self._initialized = False
            if not self.adapter.initialize(**kwargs):
                return self._fail("MT5 terminal initialization failed")
            self._initialized = True
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
            candidate_fingerprint = self._fingerprint_for_account(account)
            if (
                self._authenticated_fingerprint is not None
                and candidate_fingerprint != self._authenticated_fingerprint
            ):
                return self._fail(
                    "connected account fingerprint differs from the previously authenticated account"
                )
            self._authenticated_fingerprint = candidate_fingerprint

            self.state = ConnectionState.AUTHENTICATED
            return True
        except Exception as exc:
            return self._fail(f"MT5 connection error: {type(exc).__name__}")

    @_serialized
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
                and self._fingerprint_for_account(account) == self._authenticated_fingerprint
            )
            if not connected:
                self.state = ConnectionState.DISCONNECTED
            return connected
        except Exception:
            self.state = ConnectionState.DISCONNECTED
            return False

    @_serialized
    def ensure_connected(self) -> bool:
        if self.is_connected():
            return True
        for _ in range(self.reconnect_attempts):
            if self.connect():
                return True
        return False

    @_serialized
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
                "metadata": self._symbol_metadata(item),
            })
        return sorted(discovered, key=lambda row: row["name"].casefold())

    @_serialized
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
            "metadata": self._symbol_metadata(refreshed),
        }

    @staticmethod
    def _latest_rate_timestamp(rates: Any) -> int | None:
        if rates is None or len(rates) == 0:
            return None
        names = getattr(getattr(rates, "dtype", None), "names", None)
        if names and "time" in names:
            values = rates["time"]
        else:
            values = []
            for row in rates:
                if isinstance(row, dict):
                    values.append(row.get("time"))
                else:
                    try:
                        values.append(row[0])
                    except (TypeError, IndexError, KeyError):
                        continue
        valid = []
        for value in values:
            if hasattr(value, "item"):
                value = value.item()
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(number) and number > 0 and number.is_integer():
                valid.append(int(number))
        return max(valid) if valid else None

    @_serialized
    def fetch_rates_detailed(
        self, exact_symbol: str, timeframe: str, count: int, start_pos: int = 0
    ):
        """Fetch a bounded history window in pages and report pagination evidence."""
        if timeframe not in self._timeframes or self._timeframes[timeframe] is None:
            raise ValueError("unsupported MT5 timeframe")
        if (
            not isinstance(count, int) or isinstance(count, bool)
            or count < 1 or count > MAX_HISTORY_COUNT
        ):
            raise ValueError(
                f"count must be an integer between 1 and {MAX_HISTORY_COUNT}"
            )
        if (
            not isinstance(start_pos, int) or isinstance(start_pos, bool)
            or start_pos < 0 or start_pos + count > 1000000
        ):
            raise ValueError("start_pos must be an integer between 0 and 1000000")

        self.select_symbol(exact_symbol)
        before_timestamp = None
        snapshot_check_performed = count > HISTORY_PAGE_SIZE
        if snapshot_check_performed:
            before = self.adapter.copy_rates_from_pos(
                exact_symbol, self._timeframes[timeframe], 0, 1
            )
            before_timestamp = self._latest_rate_timestamp(before)

        pages = []
        page_sizes = []
        page_start_positions = []
        cursor = start_pos
        remaining = count
        while remaining > 0:
            page_request = min(HISTORY_PAGE_SIZE, remaining)
            page = self.adapter.copy_rates_from_pos(
                exact_symbol, self._timeframes[timeframe], cursor, page_request
            )
            if page is None or len(page) == 0:
                if not pages:
                    error = (
                        self.adapter.last_error()
                        if hasattr(self.adapter, "last_error") else "no rates"
                    )
                    raise RuntimeError(f"broker returned no rates: {error}")
                break
            page_length = len(page)
            if page_length > page_request:
                raise RuntimeError("broker returned more bars than requested for a page")
            pages.append(page)
            page_sizes.append(page_length)
            page_start_positions.append(cursor)
            remaining -= page_length
            if page_length < page_request:
                break
            cursor += page_length

        after_timestamp = None
        if snapshot_check_performed:
            after = self.adapter.copy_rates_from_pos(
                exact_symbol, self._timeframes[timeframe], 0, 1
            )
            after_timestamp = self._latest_rate_timestamp(after)
            if before_timestamp is None or after_timestamp is None:
                snapshot_stable = None
            else:
                snapshot_stable = before_timestamp == after_timestamp
        else:
            snapshot_stable = True

        if len(pages) == 1:
            rates = pages[0]
        else:
            ordered_pages = list(reversed(pages))
            try:
                import numpy as np
                # MT5 position zero is newest; older pages must precede newer
                # pages in the returned chronological sequence.
                rates = np.concatenate(ordered_pages, axis=0)
            except (ImportError, TypeError, ValueError):
                rates = _CombinedRateBatch(ordered_pages)

        metadata = {
            "requested_count": count,
            "returned_count": sum(page_sizes),
            "page_count": len(pages),
            "page_sizes": page_sizes,
            "page_start_positions": page_start_positions,
            "snapshot_check_performed": snapshot_check_performed,
            "snapshot_stable": snapshot_stable,
            "latest_bar_time_before": before_timestamp,
            "latest_bar_time_after": after_timestamp,
        }
        return rates, metadata

    def fetch_rates(
        self, exact_symbol: str, timeframe: str, count: int, start_pos: int = 0
    ):
        """Compatibility wrapper returning only the fetched broker-rate array."""
        rates, _ = self.fetch_rates_detailed(
            exact_symbol, timeframe, count, start_pos
        )
        return rates

    @_serialized
    def close(self) -> None:
        if self.adapter is not None:
            try:
                self.adapter.shutdown()
            finally:
                self._initialized = False
                self.state = ConnectionState.DISCONNECTED
