"""Append-only canonical candle storage with explicit raw-source lineage.

Canonical candles are derived records. They never replace raw broker observations.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SOURCE_CLASSES = frozenset({"broker_bar", "tick_derived", "resampled_bar"})
BAR_STATUSES = frozenset({"forming", "closed"})
VOLUME_TYPES = frozenset({"tick_volume", "real_volume", "trade_count", "unknown"})
VALIDATION_STATUSES = frozenset({"valid", "warning"})
TIMEFRAME_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


class CanonicalDataError(ValueError):
    """Raised when a canonical candle or its source lineage is invalid."""


def _utc_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise CanonicalDataError(f"{field} must be a non-empty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CanonicalDataError(f"{field} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CanonicalDataError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def validate_canonical_candle(record: dict[str, Any]) -> dict[str, Any]:
    """Validate the canonical candle contract without mutating the input."""
    if not isinstance(record, dict):
        raise CanonicalDataError("candle must be an object")

    required = (
        "instrument_id", "broker", "server_name", "account_environment",
        "broker_symbol", "timeframe", "open_time", "close_time",
        "open", "high", "low", "close", "volume", "volume_type",
        "source_class", "bar_status", "source_observation_ids",
        "transformation_version", "validation_report_id", "validation_status",
    )
    missing = [field for field in required if field not in record]
    if missing:
        raise CanonicalDataError("missing required fields: " + ", ".join(missing))

    for field in (
        "instrument_id", "broker", "server_name", "account_environment",
        "broker_symbol", "timeframe", "transformation_version", "validation_report_id",
    ):
        if not isinstance(record[field], str) or not record[field].strip():
            raise CanonicalDataError(f"{field} must be a non-empty string")

    if record["source_class"] not in SOURCE_CLASSES:
        raise CanonicalDataError("invalid source_class")
    if record["bar_status"] not in BAR_STATUSES:
        raise CanonicalDataError("invalid bar_status")
    if record["volume_type"] not in VOLUME_TYPES:
        raise CanonicalDataError("invalid volume_type")
    if record["validation_status"] not in VALIDATION_STATUSES:
        raise CanonicalDataError("invalid validation_status")

    open_time = _utc_datetime(record["open_time"], "open_time")
    close_time = _utc_datetime(record["close_time"], "close_time")
    if close_time <= open_time:
        raise CanonicalDataError("close_time must be later than open_time")
    expected_seconds = TIMEFRAME_SECONDS.get(record["timeframe"])
    if expected_seconds is None:
        raise CanonicalDataError("unsupported canonical timeframe")
    if (close_time - open_time).total_seconds() != expected_seconds:
        raise CanonicalDataError("candle boundaries do not match the declared timeframe")

    values = {}
    for field in ("open", "high", "low", "close", "volume"):
        value = record[field]
        if isinstance(value, bool):
            raise CanonicalDataError(f"{field} must be numeric")
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise CanonicalDataError(f"{field} must be numeric") from exc
        if not math.isfinite(value):
            raise CanonicalDataError(f"{field} must be finite")
        values[field] = value

    if min(values[key] for key in ("open", "high", "low", "close")) <= 0:
        raise CanonicalDataError("OHLC prices must be positive")
    if values["volume"] < 0:
        raise CanonicalDataError("volume must be non-negative")
    if values["high"] < max(values["open"], values["close"]):
        raise CanonicalDataError("high violates OHLC boundaries")
    if values["low"] > min(values["open"], values["close"]):
        raise CanonicalDataError("low violates OHLC boundaries")
    if values["high"] < values["low"]:
        raise CanonicalDataError("high must be greater than or equal to low")

    source_ids = record["source_observation_ids"]
    if (
        not isinstance(source_ids, list) or not source_ids
        or any(not isinstance(item, str) or not item.strip() for item in source_ids)
        or len(source_ids) != len(set(source_ids))
    ):
        raise CanonicalDataError("source_observation_ids must be a non-empty unique string list")

    if record["source_class"] == "broker_bar" and len(source_ids) != 1:
        raise CanonicalDataError("broker_bar candles require exactly one source observation")
    if record["source_class"] in {"tick_derived", "resampled_bar"} and not record["transformation_version"]:
        raise CanonicalDataError("derived candles require a transformation_version")

    return record


class CanonicalCandleStore:
    """Persist canonical candles and exact source links in the raw evidence DB.

    All writes are append-only. Each insertion validates that every source
    observation exists and belongs to the same instrument/account identity.
    """

    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        if not Path(self.database_path).exists():
            raise CanonicalDataError("raw observation database does not exist")
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS canonical_validation_reports (
                    report_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    broker TEXT NOT NULL,
                    server_name TEXT NOT NULL,
                    account_environment TEXT NOT NULL,
                    broker_symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    validator_version TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    report_sha256 TEXT NOT NULL,
                    generated_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS canonical_candles (
                    candle_id TEXT PRIMARY KEY,
                    instrument_id TEXT NOT NULL,
                    broker TEXT NOT NULL,
                    server_name TEXT NOT NULL,
                    account_environment TEXT NOT NULL,
                    broker_symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    open_time TEXT NOT NULL,
                    close_time TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    volume_type TEXT NOT NULL,
                    source_class TEXT NOT NULL,
                    bar_status TEXT NOT NULL,
                    transformation_version TEXT NOT NULL,
                    validation_report_id TEXT NOT NULL,
                    validation_status TEXT NOT NULL,
                    derived_at TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS canonical_candle_sources (
                    candle_id TEXT NOT NULL REFERENCES canonical_candles(candle_id),
                    observation_id TEXT NOT NULL REFERENCES raw_observations(observation_id),
                    source_ordinal INTEGER NOT NULL,
                    PRIMARY KEY (candle_id, observation_id),
                    UNIQUE (candle_id, source_ordinal)
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_canonical_identity_time
                ON canonical_candles(instrument_id, timeframe, open_time)
            """)
            connection.execute("""
                CREATE TRIGGER IF NOT EXISTS canonical_candles_require_report
                BEFORE INSERT ON canonical_candles
                WHEN NOT EXISTS (
                    SELECT 1 FROM canonical_validation_reports
                    WHERE report_id = NEW.validation_report_id
                )
                BEGIN SELECT RAISE(ABORT, 'canonical validation report is required'); END
            """)
            for table in ("canonical_validation_reports", "canonical_candles", "canonical_candle_sources"):
                connection.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_no_update
                    BEFORE UPDATE ON {table}
                    BEGIN SELECT RAISE(ABORT, 'canonical lineage is append-only'); END
                """)
                connection.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_no_delete
                    BEFORE DELETE ON {table}
                    BEGIN SELECT RAISE(ABORT, 'canonical lineage is append-only'); END
                """)

    def append_batch(
        self,
        candles: Iterable[dict[str, Any]],
        validation_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        candles = list(candles)
        if not isinstance(validation_report, dict):
            raise CanonicalDataError("a persisted validation_report is required")

        validated = [validate_canonical_candle(candle) for candle in candles]
        report_required = (
            "report_id", "batch_id", "instrument_id", "broker", "server_name",
            "account_fingerprint", "account_environment", "broker_symbol", "timeframe",
            "validator_version", "report",
        )
        missing_report = [key for key in report_required if not validation_report.get(key)]
        if missing_report:
            raise CanonicalDataError("validation_report missing: " + ", ".join(missing_report))
        report_payload = {
            "report_id": validation_report["report_id"],
            "batch_id": validation_report["batch_id"],
            "instrument_id": validation_report["instrument_id"],
            "broker": validation_report["broker"],
            "server_name": validation_report["server_name"],
            "account_fingerprint": validation_report["account_fingerprint"],
            "account_environment": validation_report["account_environment"],
            "broker_symbol": validation_report["broker_symbol"],
            "timeframe": validation_report["timeframe"],
            "validator_version": validation_report["validator_version"],
            "report": validation_report["report"],
        }
        report_json = _canonical_json(report_payload)
        report_hash = hashlib.sha256(report_json.encode("utf-8")).hexdigest()
        inserted_ids = []
        record_hashes = []
        source_lineage = []
        now = datetime.now(timezone.utc).isoformat()

        report_identity = tuple(report_payload[key] for key in (
            "instrument_id", "broker", "server_name", "account_fingerprint",
            "account_environment", "broker_symbol", "timeframe",
        ))
        if any(candle["validation_report_id"] != report_payload["report_id"] for candle in validated):
            raise CanonicalDataError("candle validation_report_id does not match supplied report")
        if any(tuple(candle[key] for key in (
            "instrument_id", "broker", "server_name", "account_fingerprint",
            "account_environment", "broker_symbol", "timeframe",
        )) != report_identity for candle in validated):
            raise CanonicalDataError("candle identity does not match validation report")

        with self._connect() as connection:
            connection.execute("""
                INSERT OR IGNORE INTO canonical_validation_reports (
                    report_id, batch_id, instrument_id, broker, server_name,
                    account_environment, broker_symbol, timeframe,
                    validator_version, report_json, report_sha256, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                report_payload["report_id"], report_payload["batch_id"],
                report_payload["instrument_id"], report_payload["broker"],
                report_payload["server_name"], report_payload["account_environment"],
                report_payload["broker_symbol"], report_payload["timeframe"],
                report_payload["validator_version"], report_json, report_hash, now,
            ))
            existing_report = connection.execute(
                "SELECT report_sha256 FROM canonical_validation_reports WHERE report_id = ?",
                (report_payload["report_id"],),
            ).fetchone()
            if existing_report is None or existing_report[0] != report_hash:
                raise CanonicalDataError("validation report ID already exists with different content")

            for candle in validated:
                source_ids = candle["source_observation_ids"]
                placeholders = ",".join("?" for _ in source_ids)
                source_rows = connection.execute(
                    f"""SELECT observation_id, batch_id, instrument_id, broker, server_name,
                               account_environment, broker_symbol, timeframe, source_type
                        FROM raw_observations
                        WHERE observation_id IN ({placeholders})""",
                    tuple(source_ids),
                ).fetchall()
                source_by_id = {row[0]: row for row in source_rows}
                if set(source_by_id) != set(source_ids):
                    raise CanonicalDataError("one or more source observations do not exist")

                for source_id in source_ids:
                    source = source_by_id[source_id]
                    expected = (
                        report_payload["instrument_id"], report_payload["broker"],
                        report_payload["server_name"], report_payload["account_fingerprint"],
                        report_payload["account_environment"], report_payload["broker_symbol"],
                    )
                    if source[1] != report_payload["batch_id"]:
                        raise CanonicalDataError("source observation batch does not match validation report")
                    actual = tuple(source[2:8])
                    if actual != expected:
                        raise CanonicalDataError(
                            "source observation identity does not match canonical candle"
                        )
                    expected_source_type = {"broker_bar": "broker_bars", "tick_derived": "broker_ticks", "resampled_bar": "broker_bars"}[candle["source_class"]]
                    if source[9] != expected_source_type:
                        raise CanonicalDataError("raw observation source type does not match canonical source class")
                    if candle["source_class"] == "broker_bar" and source[8] != candle["timeframe"]:
                        raise CanonicalDataError(
                            "broker_bar source timeframe does not match canonical timeframe"
                        )

                payload = {
                    "instrument_id": candle["instrument_id"],
                    "broker": candle["broker"],
                    "server_name": candle["server_name"],
                    "account_environment": candle["account_environment"],
                    "broker_symbol": candle["broker_symbol"],
                    "timeframe": candle["timeframe"],
                    "open_time": _utc_datetime(candle["open_time"], "open_time").isoformat(),
                    "close_time": _utc_datetime(candle["close_time"], "close_time").isoformat(),
                    "open": float(candle["open"]),
                    "high": float(candle["high"]),
                    "low": float(candle["low"]),
                    "close": float(candle["close"]),
                    "volume": float(candle["volume"]),
                    "volume_type": candle["volume_type"],
                    "source_class": candle["source_class"],
                    "bar_status": candle["bar_status"],
                    "source_observation_ids": list(candle["source_observation_ids"]),
                    "transformation_version": candle["transformation_version"],
                    "validation_report_id": candle["validation_report_id"],
                    "validation_status": candle["validation_status"],
                }
                serialized = _canonical_json(payload)
                record_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
                candle_id = str(uuid.uuid4())
                connection.execute("""
                    INSERT INTO canonical_candles (
                        candle_id, instrument_id, broker, server_name,
                        account_environment, broker_symbol, timeframe,
                        open_time, close_time, open, high, low, close, volume,
                        volume_type, source_class, bar_status,
                        transformation_version, validation_report_id, validation_status,
                        derived_at, record_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    candle_id, candle["instrument_id"], candle["broker"],
                    candle["server_name"], candle["account_environment"],
                    candle["broker_symbol"], candle["timeframe"],
                    _utc_datetime(candle["open_time"], "open_time").isoformat(),
                    _utc_datetime(candle["close_time"], "close_time").isoformat(),
                    float(candle["open"]), float(candle["high"]),
                    float(candle["low"]), float(candle["close"]),
                    float(candle["volume"]), candle["volume_type"],
                    candle["source_class"], candle["bar_status"],
                    candle["transformation_version"], candle["validation_report_id"],
                    candle["validation_status"], now, record_hash,
                ))
                for ordinal, source_id in enumerate(source_ids):
                    connection.execute("""
                        INSERT INTO canonical_candle_sources (
                            candle_id, observation_id, source_ordinal
                        ) VALUES (?, ?, ?)
                    """, (candle_id, source_id, ordinal))
                inserted_ids.append(candle_id)
                record_hashes.append(record_hash)
                for source_id in source_ids:
                    source_lineage.append({
                        "candle_id": candle_id,
                        "observation_id": source_id,
                    })

        return {
            "inserted_count": len(inserted_ids),
            "candle_ids": inserted_ids,
            "record_sha256": record_hashes,
            "source_lineage": source_lineage,
            "validation_report_id": report_payload["report_id"],
            "validation_report_sha256": report_hash,
        }

    def count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM canonical_candles"
            ).fetchone()[0])


    def get_validation_report(self, report_id: str) -> dict[str, Any] | None:
        """Return an immutable validation report after checking its stored hash."""
        if not isinstance(report_id, str) or not report_id.strip():
            raise CanonicalDataError("report_id must be a non-empty string")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT report_json, report_sha256 FROM canonical_validation_reports WHERE report_id = ?",
                (report_id,),
            ).fetchone()
        if row is None:
            return None
        report_json, report_sha256 = row
        actual_hash = hashlib.sha256(report_json.encode("utf-8")).hexdigest()
        if actual_hash != report_sha256:
            raise CanonicalDataError("stored validation report hash mismatch")
        return {
            "report": json.loads(report_json),
            "report_sha256": report_sha256,
        }

    def count_closed_for_report(self, report_id: str) -> int:
        """Count valid closed canonical candles backed by one validation report."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) FROM canonical_candles
                WHERE validation_report_id = ?
                  AND bar_status = 'closed'
                  AND validation_status = 'valid'
                """,
                (report_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    def get(self, candle_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("""
                SELECT candle_id, instrument_id, broker, server_name,
                       account_environment, broker_symbol, timeframe,
                       open_time, close_time, open, high, low, close, volume,
                       volume_type, source_class, bar_status,
                       transformation_version, validation_report_id, validation_status,
                       derived_at, record_sha256
                FROM canonical_candles WHERE candle_id = ?
            """, (candle_id,)).fetchone()
            if row is None:
                return None
            sources = connection.execute("""
                SELECT observation_id FROM canonical_candle_sources
                WHERE candle_id = ? ORDER BY source_ordinal
            """, (candle_id,)).fetchall()

        fields = (
            "candle_id", "instrument_id", "broker", "server_name",
            "account_environment", "broker_symbol", "timeframe",
            "open_time", "close_time", "open", "high", "low", "close",
            "volume", "volume_type", "source_class", "bar_status",
            "transformation_version", "validation_report_id", "validation_status",
            "derived_at", "record_sha256",
        )
        result = dict(zip(fields, row))
        result["source_observation_ids"] = [source[0] for source in sources]
        return result
