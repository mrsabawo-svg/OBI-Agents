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
from core.instrument_registry_store import InstrumentRegistryStore, metadata_fingerprint
from core.history_completeness import assess_history_completeness, TIMEFRAME_SECONDS

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
MT5_ACCOUNT_FINGERPRINT_KEY = os.environ.get("MT5_ACCOUNT_FINGERPRINT_KEY", "")
MT5_EXPECTED_ACCOUNT_FINGERPRINT = (
    os.environ.get("MT5_EXPECTED_ACCOUNT_FINGERPRINT", "").strip() or None
)


def _load_verified_session_models(raw_value):
    if not raw_value:
        return {}
    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("MT5_VERIFIED_SESSION_MODEL_JSON must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("MT5_VERIFIED_SESSION_MODEL_JSON must be an object")
    models = {}
    for instrument_id, model in parsed.items():
        if not isinstance(instrument_id, str) or not instrument_id.strip():
            raise ValueError("verified session model keys must be instrument IDs")
        if model != "continuous_24_7":
            raise ValueError(
                "verified session models may only declare continuous_24_7"
            )
        models[instrument_id] = model
    return models


# Operator assertions only: never infer a 24/7 calendar from an instrument name
# or from SessionAgent's trading eligibility rules.
MT5_VERIFIED_SESSION_MODELS = _load_verified_session_models(
    os.environ.get("MT5_VERIFIED_SESSION_MODEL_JSON", "")
)
MAX_COUNT = 20000
MIN_ANALYSIS_CLOSED_CANDLES = 60
CANONICAL_TRANSFORMATION_VERSION = "mt5-broker-bars-v1"
connector = None


def _symbol_map():
    raw = os.environ.get("MT5_SYMBOL_MAP_JSON", "{}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("MT5_SYMBOL_MAP_JSON must be valid JSON") from exc
    if not isinstance(parsed, dict) or any(
        not isinstance(k, str) or not k.strip() or k.strip() != k
        or not isinstance(v, str) or not v.strip() or v.strip() != v
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


def _bar_status(index, forming_indexes):
    """Use UTC bar-boundary evidence, not array position, to label forming bars."""
    return "forming" if index in forming_indexes else "closed"


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


def _response_row(raw, index, forming_indexes, quarantine_codes, observation_id):
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
        "bar_status": _bar_status(index, forming_indexes),
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
    connector = None
    if mt5 is None:
        return False, "MetaTrader5 package not installed"
    if not MT5_BROKER_NAME or MT5_BROKER_NAME.lower() == "unknown":
        return False, "MT5_BROKER_NAME must identify the configured broker"
    if MT5_ACCOUNT_ENVIRONMENT not in {"demo", "live", "contest"}:
        return False, "MT5_ACCOUNT_ENVIRONMENT must be demo, live, or contest"
    if not MT5_SERVER or not MT5_SERVER.strip():
        return False, "MT5_SERVER must identify the exact expected broker server"
    if len(MT5_ACCOUNT_FINGERPRINT_KEY) < 32:
        return False, "MT5_ACCOUNT_FINGERPRINT_KEY must contain at least 32 characters"
    if MT5_EXPECTED_ACCOUNT_FINGERPRINT is not None and (
        len(MT5_EXPECTED_ACCOUNT_FINGERPRINT) != 64
        or any(char not in "0123456789abcdef" for char in MT5_EXPECTED_ACCOUNT_FINGERPRINT)
    ):
        return False, "MT5_EXPECTED_ACCOUNT_FINGERPRINT must be a lowercase SHA-256 hex digest"
    if bool(MT5_LOGIN) != bool(MT5_PASSWORD):
        return False, "MT5_LOGIN and MT5_PASSWORD must be configured together"
    connector = MT5Connector(
        mt5,
        ConnectionIdentity(
            broker=MT5_BROKER_NAME,
            server=MT5_SERVER,
            environment=MT5_ACCOUNT_ENVIRONMENT,
        ),
        login=MT5_LOGIN or None,
        password=MT5_PASSWORD or None,
        server=MT5_SERVER if MT5_LOGIN and MT5_PASSWORD else None,
        terminal_path=MT5_PATH or None,
        reconnect_attempts=1,
        fingerprint_key=MT5_ACCOUNT_FINGERPRINT_KEY,
        expected_account_fingerprint=MT5_EXPECTED_ACCOUNT_FINGERPRINT,
    )
    if not connector.connect():
        return False, connector.last_error or "MT5 connection failed"
    return True, "connected"



def _instrument_verification_state(
    registry: InstrumentRegistryStore,
    account_fingerprint: str,
    instrument_id: str,
    broker_symbol: str,
    timeframe: str,
    current_metadata_sha256: str,
) -> dict:
    verification = registry.latest_verification(
        account_fingerprint, instrument_id, broker_symbol
    )
    if verification is None:
        status = "partially_verified"
        verified_timeframes = []
        evidence_refs = []
        verification_id = None
        verified_at = None
        reviewer = None
    elif verification.get("metadata_sha256") != current_metadata_sha256:
        status = "stale"
        verified_timeframes = verification.get("verified_timeframes", [])
        evidence_refs = verification.get("evidence_refs", [])
        verification_id = verification.get("verification_id")
        verified_at = verification.get("verified_at")
        reviewer = verification.get("reviewer")
    else:
        status = verification.get("verification_status", "unverified")
        verified_timeframes = verification.get("verified_timeframes", [])
        evidence_refs = verification.get("evidence_refs", [])
        verification_id = verification.get("verification_id")
        verified_at = verification.get("verified_at")
        reviewer = verification.get("reviewer")

    analysis_eligible_timeframes = sorted({
        item["timeframe"]
        for item in evidence_refs
        if (
            isinstance(item, dict)
            and item.get("timeframe") in {"5m", "15m", "1h", "4h"}
            and isinstance(item.get("closed_candle_count"), int)
            and not isinstance(item.get("closed_candle_count"), bool)
            and item["closed_candle_count"] >= MIN_ANALYSIS_CLOSED_CANDLES
        )
    })
    timeframe_verified = status == "verified" and timeframe in verified_timeframes
    analysis_eligible = (
        timeframe_verified and timeframe in analysis_eligible_timeframes
    )
    return {
        "status": status,
        "verification_id": verification_id,
        "metadata_sha256": current_metadata_sha256,
        "verified_timeframes": verified_timeframes,
        "analysis_eligible_timeframes": analysis_eligible_timeframes,
        "timeframe_verified": timeframe_verified,
        "analysis_eligible": analysis_eligible,
        "reviewer": reviewer,
        "verified_at": verified_at,
    }


@app.route("/health", methods=["GET"])
def health():
    if connector is None:
        return jsonify({"status": "disconnected", "connected": False}), 503
    connected = connector.is_connected()
    return jsonify({
        "status": "connected" if connected else "disconnected",
        "connected": connected,
        "state": connector.state.value,
        "account_identity_pinned": bool(
            connector.expected_account_fingerprint
        ),
    }), (200 if connected else 503)


@app.route("/symbols", methods=["GET"])
def get_symbols():
    if connector is None:
        return jsonify({"status": "error", "error": "MT5 connector is not initialized"}), 503
    try:
        account_identity = connector.get_account_identity()
        symbols = connector.discover_symbols()
        discovery = InstrumentRegistryStore(MT5_RAW_DB_PATH).record_discovery(
            account_identity, symbols, SYMBOL_MAP
        )
    except Exception:
        app.logger.exception("MT5 symbol discovery or registry persistence failed")
        return jsonify({
            "status": "error",
            "error": "symbol discovery or registry persistence failed",
        }), 503
    return jsonify({
        "status": "ok",
        "count": len(symbols),
        "account_identity": account_identity,
        "account_identity_pinned": account_identity.get("identity_pinned") is True,
        "discovery": discovery,
        "symbols": symbols,
    })



@app.route("/instruments/verify", methods=["POST"])
def verify_instrument():
    """Record explicit operator approval backed by immutable canonical evidence."""
    if connector is None:
        return jsonify({"status": "error", "error": "MT5 connector is not initialized"}), 503
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "error", "error": "JSON request body is required"}), 400

    instrument_id = payload.get("instrument_id")
    broker_symbol = payload.get("broker_symbol")
    reviewer = payload.get("reviewer")
    evidence_report_ids = payload.get("evidence_report_ids")
    if not isinstance(instrument_id, str) or not instrument_id.strip():
        return jsonify({"status": "error", "error": "instrument_id is required"}), 400
    if not isinstance(broker_symbol, str) or not broker_symbol.strip():
        return jsonify({"status": "error", "error": "broker_symbol is required"}), 400
    if not isinstance(reviewer, str) or not reviewer.strip():
        return jsonify({"status": "error", "error": "reviewer is required"}), 400
    if (
        not isinstance(evidence_report_ids, list)
        or not evidence_report_ids
        or any(not isinstance(value, str) or not value.strip() for value in evidence_report_ids)
        or len(set(evidence_report_ids)) != len(evidence_report_ids)
    ):
        return jsonify({
            "status": "error",
            "error": "evidence_report_ids must be a non-empty list of unique report IDs",
        }), 400

    try:
        account_identity = connector.get_account_identity()
        if account_identity.get("identity_pinned") is not True:
            return jsonify({"status": "error", "error": "account identity must be pinned first"}), 409
        account_fingerprint = account_identity["account_fingerprint"]
        if SYMBOL_MAP.get(instrument_id) != broker_symbol:
            return jsonify({
                "status": "error",
                "error": "requested instrument and broker symbol do not match the configured exact mapping",
            }), 409

        registry = InstrumentRegistryStore(MT5_RAW_DB_PATH)
        discovery = registry.latest_symbol(account_fingerprint, broker_symbol)
        if discovery is None:
            return jsonify({"status": "error", "error": "call /symbols before instrument verification"}), 409
        if discovery["mapping_conflict"] or instrument_id not in discovery["mapped_instrument_ids"]:
            return jsonify({"status": "error", "error": "instrument mapping is missing or ambiguous"}), 409

        selected_symbol = connector.select_symbol(broker_symbol)
        current_metadata_sha256 = metadata_fingerprint(
            selected_symbol.get("metadata", {})
        )
        if current_metadata_sha256 != discovery["metadata_sha256"]:
            return jsonify({
                "status": "error",
                "error": "broker contract metadata changed; rediscover and review the symbol before verification",
            }), 409

        canonical_store = CanonicalCandleStore(MT5_RAW_DB_PATH)
        evidence_refs = []
        seen_timeframes = set()
        for report_id in evidence_report_ids:
            saved = canonical_store.get_validation_report(report_id)
            if saved is None:
                return jsonify({
                    "status": "error",
                    "error": f"canonical validation report not found: {report_id}",
                }), 422
            report = saved["report"]
            quality = report.get("report") if isinstance(report, dict) else None
            history = quality.get("history_completeness") if isinstance(quality, dict) else None
            timeframe = report.get("timeframe") if isinstance(report, dict) else None
            if (
                report.get("report_id") != report_id
                or report.get("instrument_id") != instrument_id
                or report.get("broker_symbol") != broker_symbol
                or report.get("broker") != MT5_BROKER_NAME
                or report.get("server_name") != account_identity.get("server")
                or report.get("account_environment") != account_identity.get("environment")
                or timeframe not in {"5m", "15m", "1h", "4h"}
                or timeframe in seen_timeframes
                or not isinstance(quality, dict)
                or quality.get("account_fingerprint") != account_fingerprint
                or quality.get("account_identity_pinned") is not True
                or quality.get("instrument_metadata_sha256") != current_metadata_sha256
                or not isinstance(history, dict)
                or history.get("status") != "complete"
                or history.get("account_fingerprint") != account_fingerprint
                or history.get("instrument_id") != instrument_id
                or history.get("broker_symbol") != broker_symbol
                or history.get("timeframe") != timeframe
            ):
                return jsonify({
                    "status": "error",
                    "error": f"validation report does not prove complete pinned evidence for this instrument: {report_id}",
                }), 422
            closed_candle_count = canonical_store.count_closed_for_report(report_id)
            if closed_candle_count < 1:
                return jsonify({
                    "status": "error",
                    "error": f"validation report has no valid closed canonical candles: {report_id}",
                }), 422
            seen_timeframes.add(timeframe)
            evidence_refs.append({
                "report_id": report_id,
                "report_sha256": saved["report_sha256"],
                "timeframe": timeframe,
                "closed_candle_count": closed_candle_count,
            })

        verification = registry.record_verification(
            account_identity=account_identity,
            instrument_id=instrument_id,
            broker_symbol=broker_symbol,
            metadata_sha256=current_metadata_sha256,
            evidence_refs=evidence_refs,
            reviewer=reviewer,
        )
    except (ValueError, KeyError) as exc:
        return jsonify({"status": "error", "error": str(exc)}), 400
    except Exception:
        app.logger.exception("Instrument verification failed")
        return jsonify({"status": "error", "error": "instrument verification failed"}), 503

    return jsonify({
        "status": "ok",
        "instrument_verification_status": "verified",
        "verified_timeframes": verification["verified_timeframes"],
        "analysis_eligible_timeframes": sorted({
            item["timeframe"]
            for item in verification["evidence_refs"]
            if item["closed_candle_count"] >= MIN_ANALYSIS_CLOSED_CANDLES
        }),
        "verification": verification,
    }), 201


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
        account_identity = connector.get_account_identity()
        if account_identity.get("identity_pinned") is not True:
            return jsonify({
                "status": "error",
                "error": "account identity is not pinned; review /symbols and configure MT5_EXPECTED_ACCOUNT_FINGERPRINT before requesting market data",
            }), 409
        registry = InstrumentRegistryStore(MT5_RAW_DB_PATH)
        latest_discovery = registry.latest_symbol(
            account_identity["account_fingerprint"], broker_symbol
        )
        if latest_discovery is None:
            return jsonify({
                "status": "error",
                "error": "exact broker symbol has not been discovered for this account; call /symbols first",
            }), 409
        if latest_discovery["mapping_conflict"]:
            return jsonify({
                "status": "error",
                "error": "broker symbol is ambiguously mapped to multiple OBI instruments",
            }), 409
        if symbol_id not in latest_discovery["mapped_instrument_ids"]:
            return jsonify({
                "status": "error",
                "error": "exact OBI-to-broker mapping is not present in the account registry",
            }), 409
        selected_symbol = connector.select_symbol(broker_symbol)
        current_metadata = selected_symbol.get("metadata", {})
        current_metadata_sha256 = metadata_fingerprint(current_metadata)
        if current_metadata_sha256 != latest_discovery["metadata_sha256"]:
            return jsonify({
                "status": "error",
                "error": "broker symbol contract metadata changed; rediscover symbols before requesting data",
            }), 409
        instrument_verification = _instrument_verification_state(
            registry,
            account_identity["account_fingerprint"],
            symbol_id,
            broker_symbol,
            timeframe,
            current_metadata_sha256,
        )
        rates, fetch_metadata = connector.fetch_rates_detailed(
            broker_symbol, timeframe, count, start_pos
        )
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

    # Re-check the authenticated account after rate retrieval. If the
    # terminal/account changed or disconnected mid-request, withhold the batch.
    try:
        account_identity_after = connector.get_account_identity()
    except Exception:
        account_identity_after = None
    if (
        account_identity_after is None
        or account_identity_after["account_fingerprint"] != account_identity["account_fingerprint"]
        or not connector.is_connected()
    ):
        return jsonify({
            "status": "error",
            "error": "MT5 account session changed during retrieval; data withheld",
        }), 503
    retrieved_at = datetime.now(timezone.utc)
    history_completeness = assess_history_completeness(
        raw_rows,
        timeframe,
        count,
        fetch_metadata,
        start_pos=start_pos,
        retrieved_at=retrieved_at,
        session_model=MT5_VERIFIED_SESSION_MODELS.get(symbol_id, "unknown"),
    )
    history_completeness.update({
        "account_fingerprint": account_identity["account_fingerprint"],
        "instrument_id": symbol_id,
        "broker_symbol": broker_symbol,
    })
    forming_indexes = set(
        history_completeness["forming_observation_indexes"]
    )

    server_name = account_identity["server"]
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
        "account_fingerprint": account_identity["account_fingerprint"],
        "platform": "MT5",
        "broker_symbol": broker_symbol,
        "timeframe": timeframe,
        "start_pos": start_pos,
        "source_type": "broker_bars",
        "retrieved_at": retrieved_at.isoformat(),
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
            raw, index, forming_indexes,
            reasons_by_index.get(index, []),
            persisted["observation_ids"][index],
        )
        for index, raw in enumerate(raw_rows)
    ]

    validation_report_id = str(uuid.uuid4())
    quality_report = {
        "report_id": validation_report_id,
        "account_fingerprint": account_identity["account_fingerprint"],
        "account_identity_pinned": True,
        "instrument_verification_status": instrument_verification["status"],
        "instrument_verification_id": instrument_verification["verification_id"],
        "instrument_metadata_sha256": instrument_verification["metadata_sha256"],
        "verified_timeframes": instrument_verification["verified_timeframes"],
        "timeframe_verified": instrument_verification["timeframe_verified"],
        "analysis_eligible": instrument_verification["analysis_eligible"],
        "received_count": len(raw_rows),
        "quarantined_observation_count": len({
            record["observation_index"] for record in quarantine_records
        }),
        "quarantine_record_count": len(quarantine_records),
        "reason_counts": reason_counts,
        "validator_version": "2",
        "history_completeness": history_completeness,
    }
    validation_report = {
        "report_id": validation_report_id,
        "batch_id": persisted["batch_id"],
        "instrument_id": symbol_id,
        "broker": MT5_BROKER_NAME,
        "server_name": server_name,
        "account_environment": MT5_ACCOUNT_ENVIRONMENT,
        "account_fingerprint": account_identity["account_fingerprint"],
        "account_identity_pinned": True,
        "instrument_verification_status": instrument_verification["status"],
        "instrument_verification_id": instrument_verification["verification_id"],
        "instrument_metadata_sha256": instrument_verification["metadata_sha256"],
        "timeframe_verified": instrument_verification["timeframe_verified"],
        "analysis_eligible": instrument_verification["analysis_eligible"],
        "broker_symbol": broker_symbol,
        "timeframe": timeframe,
        "validator_version": quality_report["validator_version"],
        "report": quality_report,
        "history_completeness": history_completeness,
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
            "bar_status": _bar_status(index, forming_indexes),
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
        "account_identity": account_identity,
        "account_identity_pinned": True,
        "instrument_verification_status": instrument_verification["status"],
        "instrument_verification_id": instrument_verification["verification_id"],
        "instrument_metadata_sha256": instrument_verification["metadata_sha256"],
        "verified_timeframes": instrument_verification["verified_timeframes"],
        "timeframe_verified": instrument_verification["timeframe_verified"],
        "analysis_eligible": instrument_verification["analysis_eligible"],
        "analysis_eligible_timeframes": instrument_verification["analysis_eligible_timeframes"],
        "platform": "MT5",
        "timeframe": timeframe,
        "start_pos": start_pos,
        "source_type": "broker_bars",
        "volume_type": "tick_volume",
        "retrieved_at": envelope["retrieved_at"],
        "raw_persistence": persisted,
        "quality_report": quality_report,
        "history_completeness": history_completeness,
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
