"""Authenticated read-only MT5 bridge with raw-observation persistence.

This service retrieves market data only. It intentionally has no trading or
order-management routes.
"""
import ipaddress
import json
import math
import os
import secrets
import socket
from datetime import datetime, timezone, timedelta
import uuid

from flask import Flask, jsonify, request

from core.mt5_connector import ConnectionIdentity, MT5Connector
from core.raw_observation_store import RawObservationStore
from core.canonical_data_store import CanonicalCandleStore

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
TIMEFRAME_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
CANONICAL_TRANSFORMATION_VERSION = "mt5-broker-bars-v1"
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


def _finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _quarantine_reasons(raw_rows):
    """Return durable reason records for malformed broker observations."""
    reasons = []
    timestamp_indexes = {}
    for index, raw in enumerate(raw_rows):
        timestamp = _finite_number(raw.get("time"))
        timestamp_valid = timestamp is not None and timestamp > 0 and timestamp.is_integer()
        if timestamp_valid:
            try:
                datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                timestamp_valid = False
        if not timestamp_valid:
            reasons.append({
                "observation_index": index,
                "reason_code": "INVALID_TIMESTAMP",
                "reason_detail": "MT5 time must be a positive, representable Unix-second integer",
            })
        else:
            timestamp_indexes.setdefault(int(timestamp), []).append(index)

        fields = {
            "open": _finite_number(raw.get("open")),
            "high": _finite_number(raw.get("high")),
            "low": _finite_number(raw.get("low")),
            "close": _finite_number(raw.get("close")),
            "tick_volume": _finite_number(raw.get("tick_volume")),
            "real_volume": _finite_number(raw.get("real_volume", 0)),
            "spread": _finite_number(raw.get("spread", 0)),
        }
        integer_fields_valid = all(
            fields[key] is not None and fields[key].is_integer()
            for key in ("tick_volume", "real_volume", "spread")
        )
        if any(value is None for value in fields.values()) or not integer_fields_valid:
            reasons.append({
                "observation_index": index,
                "reason_code": "INVALID_NUMERIC",
                "reason_detail": "One or more MT5 numeric fields are non-finite, non-numeric, or not integer-valued",
            })

        volume_values = (fields["tick_volume"], fields["real_volume"], fields["spread"])
        if any(value is not None and value < 0 for value in volume_values):
            reasons.append({
                "observation_index": index,
                "reason_code": "INVALID_VOLUME",
                "reason_detail": "Volume and spread fields must be non-negative",
            })

        o, h, l, close = fields["open"], fields["high"], fields["low"], fields["close"]
        if (
            all(value is not None for value in (o, h, l, close))
            and (
                min(o, h, l, close) <= 0
                or h < max(o, close)
                or l > min(o, close)
                or h < l
            )
        ):
            reasons.append({
                "observation_index": index,
                "reason_code": "INVALID_OHLC",
                "reason_detail": "OHLC values violate positive-price or candle-boundary constraints",
            })

    for timestamp, indexes in timestamp_indexes.items():
        if len(indexes) > 1:
            for index in indexes:
                reasons.append({
                    "observation_index": index,
                    "reason_code": "DUPLICATE_TIMESTAMP",
                    "reason_detail": f"Timestamp {timestamp} occurs {len(indexes)} times in this batch",
                })
    return reasons


def _response_row(raw, index, latest_index, start_pos, quarantine_codes, observation_id):
    timestamp_value = _finite_number(raw.get("time"))
    timestamp = None
    if timestamp_value is not None and timestamp_value > 0 and timestamp_value.is_integer():
        try:
            timestamp = datetime.fromtimestamp(int(timestamp_value), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            timestamp = None

    def safe_int(key):
        value = _finite_number(raw.get(key, 0))
        return int(value) if value is not None and value.is_integer() else None

    open_value = _finite_number(raw.get("open"))
    high_value = _finite_number(raw.get("high"))
    low_value = _finite_number(raw.get("low"))
    close_value = _finite_number(raw.get("close"))
    tick_volume = _finite_number(raw.get("tick_volume"))

    return {
        "observation_id": observation_id,
        "timestamp": timestamp,
        "Open": open_value,
        "High": high_value,
        "Low": low_value,
        "Close": close_value,
        "Volume": tick_volume,
        "tick_volume": safe_int("tick_volume"),
        "real_volume": safe_int("real_volume"),
        "spread": safe_int("spread"),
        "volume_type": "tick_volume",
        "bar_status": _bar_status(index, latest_index, start_pos),
        "quarantine_reasons": quarantine_codes,
    }


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
    field_names = getattr(getattr(rates, "dtype", None), "names", None)
    if not field_names:
        return jsonify({
            "status": "error",
            "error": "MT5 returned an unsupported rate payload; data withheld",
        }), 503
    for rate in rates:
        raw_rows.append({
            name: (value.item() if hasattr(value, "item") else value)
            for name, value in zip(field_names, rate)
        })

    quarantine_records = _quarantine_reasons(raw_rows)
    reasons_by_index = {}
    reason_counts = {}
    for record in quarantine_records:
        index = record["observation_index"]
        reasons_by_index.setdefault(index, []).append(record["reason_code"])
        reason_counts[record["reason_code"]] = reason_counts.get(record["reason_code"], 0) + 1

    valid_timestamp_indexes = []
    for index, raw in enumerate(raw_rows):
        value = _finite_number(raw.get("time"))
        if value is not None and value > 0 and value.is_integer():
            valid_timestamp_indexes.append((int(value), index))
    latest_index = max(valid_timestamp_indexes)[1] if valid_timestamp_indexes else None

    # Re-check the authenticated account after rate retrieval. If the
    # terminal/account changed or disconnected mid-request, withhold the batch.
    account = mt5.account_info()
    if account is None or not connector.is_connected():
        return jsonify({
            "status": "error",
            "error": "MT5 account session changed during retrieval; data withheld",
        }), 503
    server_name = str(getattr(account, "server", "") or "")
    if not server_name:
        return jsonify({
            "status": "error",
            "error": "broker server identity unavailable; data withheld",
        }), 503
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
        persisted = RawObservationStore(MT5_RAW_DB_PATH).append_batch(
            envelope, raw_rows, quarantine_records=quarantine_records
        )
    except Exception:
        app.logger.exception("Raw observation persistence failed")
        return jsonify({
            "status": "error",
            "error": "raw observation persistence failed; data withheld",
        }), 503

    response_rows = [
        _response_row(
            raw, index, latest_index, start_pos,
            reasons_by_index.get(index, []),
            persisted["observation_ids"][index],
        )
        for index, raw in enumerate(raw_rows)
    ]

    validation_report_id = str(uuid.uuid4())
    quality_report = {
        "report_id": validation_report_id,
        "received_count": len(raw_rows),
        "quarantined_observation_count": len({
            record["observation_index"] for record in quarantine_records
        }),
        "quarantine_record_count": len(quarantine_records),
        "reason_counts": reason_counts,
        "validator_version": "1",
    }
    validation_report = {
        "report_id": validation_report_id,
        "batch_id": persisted["batch_id"],
        "instrument_id": symbol_id,
        "broker": MT5_BROKER_NAME,
        "server_name": server_name,
        "account_environment": MT5_ACCOUNT_ENVIRONMENT,
        "broker_symbol": broker_symbol,
        "timeframe": timeframe,
        "validator_version": quality_report["validator_version"],
        "report": quality_report,
    }

    canonical_records = []
    for index, raw in enumerate(raw_rows):
        if reasons_by_index.get(index):
            continue
        timestamp_value = _finite_number(raw.get("time"))
        numeric_values = {
            key: _finite_number(raw.get(key))
            for key in ("open", "high", "low", "close", "tick_volume")
        }
        if (
            timestamp_value is None
            or not timestamp_value.is_integer()
            or timestamp_value <= 0
            or any(value is None for value in numeric_values.values())
        ):
            continue
        open_time = datetime.fromtimestamp(
            int(timestamp_value), tz=timezone.utc
        )
        canonical_records.append({
            "instrument_id": symbol_id,
            "broker": MT5_BROKER_NAME,
            "server_name": server_name,
            "account_environment": MT5_ACCOUNT_ENVIRONMENT,
            "broker_symbol": broker_symbol,
            "timeframe": timeframe,
            "open_time": open_time.isoformat(),
            "close_time": (
                open_time + timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
            ).isoformat(),
            "open": numeric_values["open"],
            "high": numeric_values["high"],
            "low": numeric_values["low"],
            "close": numeric_values["close"],
            "volume": numeric_values["tick_volume"],
            "volume_type": "tick_volume",
            "source_class": "broker_bar",
            "bar_status": _bar_status(index, latest_index, start_pos),
            "source_observation_ids": [persisted["observation_ids"][index]],
            "transformation_version": CANONICAL_TRANSFORMATION_VERSION,
            "validation_report_id": validation_report_id,
            "validation_status": "valid",
        })

    try:
        canonical_persistence = CanonicalCandleStore(
            MT5_RAW_DB_PATH
        ).append_batch(canonical_records, validation_report=validation_report)
    except Exception:
        app.logger.exception("Canonical candle persistence failed")
        return jsonify({
            "status": "error",
            "error": "canonical candle persistence failed; data withheld",
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
        "quality_report": quality_report,
        "canonical_persistence": {
            "persisted": True,
            **canonical_persistence,
        },
        "data": response_rows,
    })


if __name__ == "__main__":
    ok, message = init_mt5()
    if not ok:
        raise SystemExit("[BRIDGE] Refusing to start: " + message)
    if not _is_loopback(MT5_BRIDGE_HOST) and not MT5_BRIDGE_TOKEN:
        raise SystemExit("[BRIDGE] Non-loopback binding requires MT5_BRIDGE_TOKEN")
    port = int(os.environ.get("PORT", "5000"))
    from waitress import serve
    serve(app, host=MT5_BRIDGE_HOST, port=port, threads=8)
