"""Authenticated read-only MT5 bridge with raw-observation persistence.

This service retrieves market data only. It intentionally has no trading or
order-management routes.
"""
import ipaddress
import json
import os
import secrets
import socket
from datetime import datetime, timezone

from flask import Flask, jsonify, request

from core.mt5_connector import ConnectionIdentity, MT5Connector
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
connector = None


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
        return ipaddress.ip_address(socket.gethostbyname(host)).is_loopback
    except (ValueError, OSError):
        return False


def _authorized():
    if not MT5_BRIDGE_TOKEN:
        return _is_loopback(MT5_BRIDGE_HOST)
    supplied = request.headers.get("Authorization", "")
    return secrets.compare_digest(supplied, "Bearer " + MT5_BRIDGE_TOKEN)


def _bar_status(index, latest_index, start_pos):
    """Only position-zero requests include MT5's current, still-forming bar."""
    return "forming" if start_pos == 0 and index == latest_index else "closed"


@app.before_request
def require_bridge_authentication():
    if request.endpoint == "health":
        return None
    if not _authorized():
        return jsonify({"status": "error", "error": "unauthorized"}), 401
    return None


def init_mt5():
    global connector
    if mt5 is None:
        return False, "MetaTrader5 package not installed"
    if not MT5_BROKER_NAME or MT5_BROKER_NAME.lower() == "unknown":
        return False, "MT5_BROKER_NAME must identify the configured broker"
    if MT5_ACCOUNT_ENVIRONMENT not in {"demo", "live", "contest"}:
        return False, "MT5_ACCOUNT_ENVIRONMENT must be demo, live, or contest"
    connector = MT5Connector(
        mt5,
        ConnectionIdentity(
            broker=MT5_BROKER_NAME,
            server=MT5_SERVER,
            environment=MT5_ACCOUNT_ENVIRONMENT,
        ),
        login=MT5_LOGIN or None,
        password=MT5_PASSWORD or None,
        server=MT5_SERVER or None,
        terminal_path=MT5_PATH or None,
        reconnect_attempts=1,
    )
    if not connector.connect():
        return False, connector.last_error or "MT5 connection failed"
    return True, "connected"


@app.route("/health", methods=["GET"])
def health():
    if connector is None:
        return jsonify({"status": "disconnected", "connected": False}), 503
    connected = connector.is_connected()
    return jsonify({
        "status": "connected" if connected else "disconnected",
        "connected": connected,
        "state": connector.state.value,
    }), (200 if connected else 503)


@app.route("/symbols", methods=["GET"])
def get_symbols():
    if connector is None:
        return jsonify({"status": "error", "error": "MT5 connector is not initialized"}), 503
    try:
        symbols = connector.discover_symbols()
    except Exception:
        app.logger.exception("MT5 symbol discovery failed")
        return jsonify({"status": "error", "error": "symbol discovery failed"}), 503
    return jsonify({"status": "ok", "count": len(symbols), "symbols": symbols})


@app.route("/ohlcv", methods=["GET"])
def get_ohlcv():
    symbol_id = request.args.get("symbol", "").strip()
    timeframe = request.args.get("timeframe", "").lower().strip()
    try:
        count = int(request.args.get("count", "300"))
        start_pos = int(request.args.get("start_pos", "0"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "error": "count and start_pos must be integers"}), 400

    if not symbol_id:
        return jsonify({"status": "error", "error": "symbol is required"}), 400
    if timeframe not in {"5m", "15m", "1h", "4h"}:
        return jsonify({"status": "error", "error": "unsupported timeframe"}), 400
    if count < 1 or count > MAX_COUNT:
        return jsonify({"status": "error", "error": f"count must be between 1 and {MAX_COUNT}"}), 400
    if connector is None:
        return jsonify({"status": "error", "error": "MT5 connector is not initialized"}), 503

    broker_symbol = SYMBOL_MAP.get(symbol_id)
    if not broker_symbol:
        return jsonify({
            "status": "error",
            "error": "instrument is not mapped to an exact broker symbol",
            "symbol_id": symbol_id,
        }), 404

    try:
        rates = connector.fetch_rates(broker_symbol, timeframe, count, start_pos)
        symbol_info = mt5.symbol_info(broker_symbol)
    except ValueError as exc:
        return jsonify({"status": "error", "error": str(exc)}), 400
    except LookupError:
        return jsonify({"status": "error", "error": "broker symbol unavailable"}), 404
    except Exception:
        app.logger.exception("MT5 rate retrieval failed")
        return jsonify({"status": "error", "error": "broker rate retrieval failed"}), 503

    raw_rows = []
    response_rows = []
    latest_index = max(range(len(rates)), key=lambda index: int(rates[index]["time"]))
    for index, rate in enumerate(rates):
        raw = {
            name: (value.item() if hasattr(value, "item") else value)
            for name, value in zip(rates.dtype.names, rate)
        }
        raw_rows.append(raw)
        timestamp = datetime.fromtimestamp(int(raw["time"]), tz=timezone.utc)
        response_rows.append({
            "timestamp": timestamp.isoformat(),
            "Open": float(raw["open"]),
            "High": float(raw["high"]),
            "Low": float(raw["low"]),
            "Close": float(raw["close"]),
            "Volume": float(raw["tick_volume"]),
            "tick_volume": int(raw["tick_volume"]),
            "real_volume": int(raw.get("real_volume", 0)),
            "spread": int(raw.get("spread", 0)),
            "volume_type": "tick_volume",
            "bar_status": _bar_status(index, latest_index, start_pos),
        })

    account = mt5.account_info()
    server_name = str(getattr(account, "server", "") or "")
    envelope = {
        "instrument_id": symbol_id,
        "broker": MT5_BROKER_NAME,
        "server_name": server_name,
        "account_environment": MT5_ACCOUNT_ENVIRONMENT,
        "platform": "MT5",
        "broker_symbol": broker_symbol,
        "timeframe": timeframe,
        "start_pos": start_pos,
        "source_type": "broker_bars",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "batch_id": None,
    }

    try:
        persisted = RawObservationStore(MT5_RAW_DB_PATH).append_batch(envelope, raw_rows)
    except Exception:
        app.logger.exception("Raw observation persistence failed")
        return jsonify({
            "status": "error",
            "error": "raw observation persistence failed; data withheld",
        }), 503

    return jsonify({
        "status": "ok",
        "symbol_id": symbol_id,
        "broker": MT5_BROKER_NAME,
        "broker_symbol": broker_symbol,
        "broker_display_name": str(getattr(symbol_info, "description", "") or ""),
        "server_name": server_name,
        "account_environment": MT5_ACCOUNT_ENVIRONMENT,
        "platform": "MT5",
        "timeframe": timeframe,
        "start_pos": start_pos,
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
