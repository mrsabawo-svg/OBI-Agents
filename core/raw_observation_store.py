"""Append-only SQLite storage for original broker observations.

This is an evidence store, not a canonical candle store. It deliberately keeps
repeated and conflicting observations rather than selecting a winner.
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


SCHEMA_VERSION = "2"


def _json_safe(value: Any) -> Any:
    if hasattr(value, "item") and callable(value.item):
        return _json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return {"__non_finite_float__": repr(value)}
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


class RawObservationStore:
    """Persist source observations before cleaning or feature calculation.

    UPDATE and DELETE are blocked by SQLite triggers. Corrections/re-fetches
    must be inserted as new observations. The database file must be placed on
    durable storage by the deployment operator.
    """

    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS raw_observations (
                    observation_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    broker TEXT NOT NULL,
                    server_name TEXT,
                    account_fingerprint TEXT,
                    account_environment TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    broker_symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_timestamp TEXT,
                    retrieved_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    schema_version TEXT NOT NULL
                )
            """)
            columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(raw_observations)"
                ).fetchall()
            }
            if "account_fingerprint" not in columns:
                connection.execute(
                    "ALTER TABLE raw_observations ADD COLUMN account_fingerprint TEXT"
                )
            connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_raw_observations_instrument_time
                ON raw_observations(instrument_id, source_timestamp)
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS quarantine_records (
                    quarantine_id TEXT PRIMARY KEY,
                    observation_id TEXT NOT NULL REFERENCES raw_observations(observation_id),
                    batch_id TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    reason_detail TEXT,
                    quarantined_at TEXT NOT NULL,
                    validator_version TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_quarantine_observation
                ON quarantine_records(observation_id)
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_quarantine_batch_reason
                ON quarantine_records(batch_id, reason_code)
            """)
            connection.execute("""
                CREATE TRIGGER IF NOT EXISTS quarantine_records_no_update
                BEFORE UPDATE ON quarantine_records
                BEGIN SELECT RAISE(ABORT, 'quarantine records are append-only'); END
            """)
            connection.execute("""
                CREATE TRIGGER IF NOT EXISTS quarantine_records_no_delete
                BEFORE DELETE ON quarantine_records
                BEGIN SELECT RAISE(ABORT, 'quarantine records are append-only'); END
            """)
            connection.execute("""
                CREATE TRIGGER IF NOT EXISTS raw_observations_no_update
                BEFORE UPDATE ON raw_observations
                BEGIN SELECT RAISE(ABORT, 'raw observations are append-only'); END
            """)
            connection.execute("""
                CREATE TRIGGER IF NOT EXISTS raw_observations_no_delete
                BEFORE DELETE ON raw_observations
                BEGIN SELECT RAISE(ABORT, 'raw observations are append-only'); END
            """)

    def append_batch(
        self,
        envelope: dict[str, Any],
        observations: Iterable[dict[str, Any]],
        quarantine_records: Iterable[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        required = (
            "instrument_id", "broker", "account_environment", "platform",
            "broker_symbol", "timeframe", "source_type",
        )
        missing = [key for key in required if not envelope.get(key)]
        if missing:
            raise ValueError("missing source envelope fields: " + ", ".join(missing))

        observations = list(observations)
        quarantine_records = list(quarantine_records or [])
        allowed_reasons = {
            "INVALID_TIMESTAMP", "INVALID_NUMERIC", "INVALID_OHLC",
            "INVALID_VOLUME", "DUPLICATE_TIMESTAMP",
        }
        for record in quarantine_records:
            if not isinstance(record, dict):
                raise ValueError("each quarantine record must be an object")
            index = record.get("observation_index")
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(observations):
                raise ValueError("quarantine observation_index is out of range")
            if record.get("reason_code") not in allowed_reasons:
                raise ValueError("unknown quarantine reason_code")

        batch_id = str(envelope.get("batch_id") or uuid.uuid4())
        retrieved_at = str(envelope.get("retrieved_at") or
                           datetime.now(timezone.utc).isoformat())
        inserted_ids: list[str] = []
        hashes: list[str] = []
        quarantine_ids: list[str] = []
        quarantine_links: list[dict[str, str]] = []
        quarantine_time = datetime.now(timezone.utc).isoformat()

        with self._connect() as connection:
            for observation in observations:
                if not isinstance(observation, dict):
                    raise ValueError("each observation must be an object")
                payload_json = _canonical_json(observation)
                payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
                observation_id = str(uuid.uuid4())
                source_timestamp = observation.get("time")
                if source_timestamp is None:
                    source_timestamp = observation.get("timestamp")
                if source_timestamp is not None:
                    source_timestamp = str(source_timestamp)

                connection.execute("""
                    INSERT INTO raw_observations (
                        observation_id, batch_id, instrument_id, broker,
                        server_name, account_fingerprint, account_environment,
                        platform, broker_symbol, timeframe, source_type,
                        source_timestamp, retrieved_at, payload_json,
                        payload_sha256, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    observation_id, batch_id, envelope["instrument_id"],
                    envelope["broker"], envelope.get("server_name"),
                    envelope.get("account_fingerprint"),
                    envelope["account_environment"], envelope["platform"],
                    envelope["broker_symbol"], envelope["timeframe"],
                    envelope["source_type"], source_timestamp, retrieved_at,
                    payload_json, payload_hash, SCHEMA_VERSION,
                ))
                inserted_ids.append(observation_id)
                hashes.append(payload_hash)

            for record in quarantine_records:
                quarantine_id = str(uuid.uuid4())
                observation_id = inserted_ids[record["observation_index"]]
                connection.execute("""
                    INSERT INTO quarantine_records (
                        quarantine_id, observation_id, batch_id, reason_code,
                        reason_detail, quarantined_at, validator_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    quarantine_id, observation_id, batch_id,
                    record["reason_code"], str(record.get("reason_detail", "")),
                    quarantine_time, str(record.get("validator_version", "1")),
                ))
                quarantine_ids.append(quarantine_id)
                quarantine_links.append({
                    "quarantine_id": quarantine_id,
                    "observation_id": observation_id,
                    "reason_code": record["reason_code"],
                })

        return {
            "batch_id": batch_id,
            "inserted_count": len(inserted_ids),
            "observation_ids": inserted_ids,
            "payload_sha256": hashes,
            "quarantine_count": len(quarantine_ids),
            "quarantine_ids": quarantine_ids,
            "quarantine_records": quarantine_links,
            "schema_version": SCHEMA_VERSION,
            "persisted": True,
        }

    def quarantine_count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM quarantine_records"
            ).fetchone()[0])

    def get_quarantine_records(self, observation_id: str | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT quarantine_id, observation_id, batch_id, reason_code,
                   reason_detail, quarantined_at, validator_version
            FROM quarantine_records
        """
        params: tuple = ()
        if observation_id is not None:
            query += " WHERE observation_id = ?"
            params = (observation_id,)
        query += " ORDER BY quarantined_at, quarantine_id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        fields = (
            "quarantine_id", "observation_id", "batch_id", "reason_code",
            "reason_detail", "quarantined_at", "validator_version",
        )
        return [dict(zip(fields, row)) for row in rows]

    def count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM raw_observations"
            ).fetchone()[0])

    def get(self, observation_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("""
                SELECT observation_id, batch_id, instrument_id, broker,
                       server_name, account_fingerprint, account_environment,
                       platform, broker_symbol, timeframe, source_type,
                       source_timestamp, retrieved_at, payload_json,
                       payload_sha256, schema_version
                FROM raw_observations WHERE observation_id = ?
            """, (observation_id,)).fetchone()
        if row is None:
            return None
        fields = (
            "observation_id", "batch_id", "instrument_id", "broker",
            "server_name", "account_fingerprint", "account_environment",
            "platform", "broker_symbol", "timeframe", "source_type",
            "source_timestamp", "retrieved_at",
            "payload_json", "payload_sha256", "schema_version",
        )
        result = dict(zip(fields, row))
        result["payload"] = json.loads(result.pop("payload_json"))
        return result
