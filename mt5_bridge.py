"""Authenticated local MT5 bridge with raw-observation persistence.

The bridge is an adapter to an already broker-connected MetaTrader 5 terminal.
It is not a broker and cannot supply market data without a broker session.
"""
import hashlib
import json
import os
import secrets
import socket
from datetime import datetime, timezone

import pytz
from flask import Flask, jsonify, request

from core.raw_observation_store import RawObservationStore

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

app = Flask(__name__)

MT5_LOGIN = int(os.environ.get("MT5_LOGIN", "0"))
MT5_PASSWORD = os.environ.get("MT5_PASSWORD", "")
MT5_SERVER = os.environ.get("MT5_SERVER", "")
MT5_PATH = os.environ.get("MT5_PATH", "")
MT5_BRIDGE_TOKEN = os.environ.get("MT5_BRIDGE_TOKEN", "")
MT5_BRIDGE_HOST = os.environ.get("MT5_BRIDGE_HOST", "127.0.0.1")
MT5_BROKER_NAME = os.environ.get("MT5_BROKER_NAME", "unknown")
MT5_ACCOUNT_ENVIRONMENT = os.environ.get("MT5_ACCOUNT_ENVIRONMENT", "unknown")
MT5_RAW_DB_PATH = os.environ.get("MT5_RAW_DB_PATH", "data/raw_observations.sqlite3")
MAX_COUNT = 2000

TIMEFRAME_MAP = {
    "5m": getattr(mt5, "TIMEFRAME_M5", 5) if mt5 else 5,
    "15m": getattr(mt5, "TIMEFRAME_M15", 15) if mt5 else 15,
    "1h": getattr(mt5, "TIMEFRAME_H1", 60) if mt5 else 60,
    "4h": getattr(mt5, "TIMEFRAME_H4", 240) if mt5 else 240,
}


def _symbol_map():
    raw = os.environ.get("MT5_SYMBOL_MAP_JSON", "{}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("MT5_SYMBOL_MAP_JSON must be valid JSON") from exc
    if not isinstance(parsed, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) or not v.strip()
        for k, v in parsed.items()
    ):
        raise RuntimeError("MT5_SYMBOL_MAP_JSON must map internal IDs to exact broker symbols")
    return parsed


SYMBOL_MAP = _symbol_map()


def _is_loopback(host):
    try:
        return ipaddress_is_loopback(host)
    except ValueError:
        return False


def ipaddress_is_loopback(host):
    import ipaddress
    return ipaddress.ip_address(socket.gethostbyname(host)).is_loopback


def _authorized():
    # Loopback-only deployment is the default trust boundary. If a token is
    # configured, enforce it even for local callers.
    if not MT5_BRIDGE_TOKEN:
        return _is_loopback(MT5_BRIDGE_HOST)
    supplied = request.headers.get("Authorization", "")
    expected = "Bearer " + MT5_BRIDGE_TOKEN
    return secrets.compare_digest(supplied, expected)


@app.before_request
def require_bridge_authentication():
    if request.endpoint == "health":
        # Health reveals only process/connection status, not account metadata.
        return None
    if not _authorized():
        return jsonify({"status": "error", "error": "unauthorized"}), 401
    return None


def init_mt5():
    if mt5 is None:
        return False, "MetaTrader5 package not installed"

    init_kwargs = {}
    if MT5_PATH:
        init_kwargs["path"] = MT5_PATH
    if not mt5.initialize(**init_kwargs):
        return False, f"MT5 initialize failed: {mt5.last_error()}"

    # Login is optional only when the terminal is already authenticated.
    if MT5_LOGIN or MT5_PASSWORD or MT5_SERVER:
        if not (MT5_LOGIN and MT5_PASSWORD and MT5_SERVER):
            return False, "MT5_LOGIN, MT5_PASSWORD and MT5_SERVER must be configured together"
        if not mt5.login(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
            return False, f"MT5 login failed: {mt5.last_error()}"

    terminal = mt5.terminal_info()
    account = mt5.account_info()
    if terminal is None or account is None or not terminal.connected:
        return False, "MT5 terminal is not connected to an authenticated broker account"
    return True, "connected"


@app.route("/health", methods=["GET"])
def health():
    if mt5 is None:
        return jsonify({"status": "error", "message": "MetaTrader5 library not installed"}), 500
    terminal = mt5.terminal_info()
    account = mt5.account_info()
    connected = bool(terminal and account and terminal.connected)
    return jsonify({
        "status": "connected" if connected else "disconnected",
        "connected": connected,
    }), (200 if connected else 503)


@app.route("/ohlcv", methods=["GET"])
def get_ohlcv():
    symbol_id = request.args.get("symbol", "").strip()
    timeframe = request.args.get("timeframe", "").lower().strip()
    try:
        count = int(request.args.get("count", "300"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "error": "count must be an integer"}), 400

    if not symbol_id:
        return jsonify({"status": "error", "error": "symbol is required"}), 400
    if timeframe not in TIMEFRAME_MAP:
        return jsonify({"status": "error", "error": "unsupported timeframe"}), 400
    if count < 1 or count > MAX_COUNT:
        return jsonify({"status": "error", "error": f"count must be between 1 and {MAX_COUNT}"}), 400
    if mt5 is None:
        return jsonify({"status": "error", "error": "MetaTrader5 package not installed"}), 503

    broker_symbol = SYMBOL_MAP.get(symbol_id)
    if not broker_symbol:
        return jsonify({
            "status": "error",
            "error": "instrument is not mapped to an exact broker symbol",
            "symbol_id": symbol_id,
        }), 404

    terminal = mt5.terminal_info()
    account = mt5.account_info()
    if terminal is None or account is None or not terminal.connected:
        return jsonify({"status": "error", "error": "MT5 broker session disconnected"}), 503

    symbol_info = mt5.symbol_info(broker_symbol)
    if symbol_info is None or not mt5.symbol_select(broker_symbol, True):
        return jsonify({"status": "error", "error": "broker symbol unavailable"}), 404

    rates = mt5.copy_rates_from_pos(broker_symbol, TIMEFRAME_MAP[timeframe], 0, count)
    if rates is None or len(rates) == 0:
        return jsonify({
            "status": "error",
            "error": "broker returned no rates",
            "mt5_error": str(mt5.last_error()),
        }), 502

    raw_rows = []
    response_rows = []
    for index, rate in enumerate(rates):
        raw = {name: (value.item() if hasattr(value, "item") else value)
               for name, value in zip(rates.dtype.names, rate)}
        raw_rows.append(raw)
        timestamp = datetime.fromtimestamp(int(raw["time"]), tz=timezone.utc)
        response_rows.append({
            "timestamp": timestamp.isoformat(),
            "Open": float(raw["open"]),
            "High": float(raw["high"]),
            "Low": float(raw["low"]),
            "Close": float(raw["close"]),
            "Volume": float(raw["tick_volume"]),  # compatibility; explicitly tick volume
            "tick_volume": int(raw["tick_volume"]),
            "real_volume": int(raw.get("real_volume", 0)),
            "spread": int(raw.get("spread", 0)),
            "volume_type": "tick_volume",
            "bar_status": "forming" if index == len(rates) - 1 else "closed",
        })

    server_name = str(getattr(account, "server", "") or MT5_SERVER or "unknown")
    account_environment = MT5_ACCOUNT_ENVIRONMENT
    envelope = {
        "instrument_id": symbol_id,
        "broker": MT5_BROKER_NAME,
        "server_name": server_name,
        "account_environment": account_environment,
        "platform": "MT5",
        "broker_symbol": broker_symbol,
        "timeframe": timeframe,
        "source_type": "broker_bars",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "batch_id": None,
    }

    try:
        persisted = RawObservationStore(MT5_RAW_DB_PATH).append_batch(envelope, raw_rows)
    except Exception as exc:
        app.logger.exception("Raw observation persistence failed")
        return jsonify({
            "status": "error",
            "error": "raw observation persistence failed; data withheld",
            "detail": str(exc),
        }), 503

    return jsonify({
        "status": "ok",
        "symbol_id": symbol_id,
        "broker": MT5_BROKER_NAME,
        "broker_symbol": broker_symbol,
        "broker_display_name": str(getattr(symbol_info, "description", "") or ""),
        "server_name": server_name,
        "account_environment": account_environment,
        "platform": "MT5",
        "timeframe": timeframe,
        "source_type": "broker_bars",
        "volume_type": "tick_volume",
        "retrieved_at": envelope["retrieved_at"],
        "raw_persistence": persisted,
        "data": response_rows,
    })


if __name__ == "__main__":
    ok, message = init_mt5()
    if not ok:
        raise SystemExit("[BRIDGE] Refusing to start: " + message)

    if not _is_loopback(MT5_BRIDGE_HOST) and not MT5_BRIDGE_TOKEN:
        raise SystemExit("[BRIDGE] Non-loopback binding requires MT5_BRIDGE_TOKEN")
    port = int(os.environ.get("PORT", "5000"))
    app.run(host=MT5_BRIDGE_HOST, port=port)
