"""Append-only account and instrument discovery registry for the MT5 bridge.

Discovery is evidence, not operator approval. This store never promotes an
instrument to verified merely because MT5 returned it.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REGISTRY_VERSION = "obi-instrument-registry-v3"


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def metadata_fingerprint(metadata: dict[str, Any]) -> str:
    """Hash stable contract metadata; exclude transient Market Watch flags."""
    stable = {
        key: value for key, value in metadata.items()
        if key not in {"selected", "visible"}
    }
    return hashlib.sha256(_json(stable).encode("utf-8")).hexdigest()


class InstrumentRegistryStore:
    """Persist immutable account observations and account-scoped symbol snapshots."""

    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self):
        with self._connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS mt5_account_observations (
                    observation_id TEXT PRIMARY KEY,
                    account_fingerprint TEXT NOT NULL,
                    broker_company TEXT NOT NULL,
                    server_name TEXT NOT NULL,
                    account_environment TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    identity_json TEXT NOT NULL,
                    registry_version TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_mt5_account_fingerprint_time
                ON mt5_account_observations(account_fingerprint, observed_at)
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS mt5_instrument_discoveries (
                    discovery_id TEXT PRIMARY KEY,
                    account_observation_id TEXT NOT NULL
                        REFERENCES mt5_account_observations(observation_id),
                    account_fingerprint TEXT NOT NULL,
                    broker_symbol TEXT NOT NULL,
                    description TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    metadata_sha256 TEXT NOT NULL,
                    mapped_instrument_ids_json TEXT NOT NULL,
                    mapping_conflict INTEGER NOT NULL,
                    availability_status TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    registry_version TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_mt5_instrument_discovery_lookup
                ON mt5_instrument_discoveries(
                    account_fingerprint, broker_symbol, discovered_at
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS mt5_instrument_verifications (
                    verification_id TEXT PRIMARY KEY,
                    account_fingerprint TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    broker_symbol TEXT NOT NULL,
                    metadata_sha256 TEXT NOT NULL,
                    verified_timeframes_json TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    reviewer TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    registry_version TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_mt5_instrument_verification_lookup
                ON mt5_instrument_verifications(
                    account_fingerprint, instrument_id, broker_symbol, verified_at
                )
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS mt5_instrument_verifications_no_update
                BEFORE UPDATE ON mt5_instrument_verifications
                BEGIN SELECT RAISE(ABORT, 'instrument verification evidence is append-only'); END
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS mt5_instrument_verifications_no_delete
                BEFORE DELETE ON mt5_instrument_verifications
                BEGIN SELECT RAISE(ABORT, 'instrument verification evidence is append-only'); END
            """)
            for table in ("mt5_account_observations", "mt5_instrument_discoveries"):
                db.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_no_update
                    BEFORE UPDATE ON {table}
                    BEGIN SELECT RAISE(ABORT, 'registry evidence is append-only'); END
                """)
                db.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_no_delete
                    BEFORE DELETE ON {table}
                    BEGIN SELECT RAISE(ABORT, 'registry evidence is append-only'); END
                """)

    def record_discovery(
        self,
        account_identity: dict[str, Any],
        symbols: list[dict[str, Any]],
        symbol_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        required = (
            "account_fingerprint", "broker_company", "server",
            "environment", "platform",
        )
        if any(not account_identity.get(key) for key in required):
            raise ValueError("complete verified account identity is required")
        identity_pinned = account_identity.get("identity_pinned", False)
        if not isinstance(identity_pinned, bool):
            raise ValueError("identity_pinned must be a boolean")
        fingerprint = account_identity.get("account_fingerprint")
        if (
            not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in fingerprint)
        ):
            raise ValueError("account_fingerprint must be a lowercase SHA-256 hex digest")
        if not isinstance(symbols, list):
            raise ValueError("symbols must be a list")
        symbol_map = symbol_map or {}
        if not isinstance(symbol_map, dict):
            raise ValueError("symbol_map must be an object")
        if any(
            not isinstance(k, str) or not k.strip()
            or not isinstance(v, str) or not v.strip()
            for k, v in symbol_map.items()
        ):
            raise ValueError("symbol_map must contain exact non-empty symbol names")

        reverse_map: dict[str, list[str]] = {}
        for instrument_id, broker_symbol in symbol_map.items():
            reverse_map.setdefault(broker_symbol, []).append(instrument_id)

        names = [item.get("name") for item in symbols if isinstance(item, dict)]
        if len(names) != len(symbols) or any(not isinstance(name, str) or not name for name in names):
            raise ValueError("each discovered symbol requires an exact name")
        if len(set(names)) != len(names):
            raise ValueError("duplicate broker symbol names in discovery response")

        observed_at = datetime.now(timezone.utc).isoformat()
        account_observation_id = str(uuid.uuid4())
        account_fingerprint = account_identity["account_fingerprint"]
        discoveries = []
        with self._connect() as db:
            db.execute("""
                INSERT INTO mt5_account_observations (
                    observation_id, account_fingerprint, broker_company,
                    server_name, account_environment, platform, observed_at,
                    identity_json, registry_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                account_observation_id, account_fingerprint,
                account_identity["broker_company"], account_identity["server"],
                account_identity["environment"], account_identity["platform"],
                observed_at, _json(account_identity), REGISTRY_VERSION,
            ))
            for symbol in symbols:
                name = symbol["name"]
                metadata = symbol.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = {
                        "name": name,
                        "description": str(symbol.get("description") or ""),
                        "selected": bool(symbol.get("selected", False)),
                        "visible": bool(symbol.get("visible", False)),
                    }
                mapped_ids = sorted(reverse_map.get(name, []))
                selected = bool(symbol.get("selected", metadata.get("selected", False)))
                visible = bool(symbol.get("visible", metadata.get("visible", False)))
                availability = (
                    "selectable" if selected else
                    "visible_in_market_watch" if visible else "discovered"
                )
                discovery_id = str(uuid.uuid4())
                fingerprint = metadata_fingerprint(metadata)
                db.execute("""
                    INSERT INTO mt5_instrument_discoveries (
                        discovery_id, account_observation_id, account_fingerprint,
                        broker_symbol, description, metadata_json, metadata_sha256,
                        mapped_instrument_ids_json, mapping_conflict,
                        availability_status, verification_status, discovered_at,
                        registry_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    discovery_id, account_observation_id, account_fingerprint,
                    name, str(symbol.get("description") or ""),
                    _json(metadata), fingerprint, _json(mapped_ids),
                    int(len(mapped_ids) > 1), availability,
                    "partially_verified" if mapped_ids else "unverified",
                    observed_at, REGISTRY_VERSION,
                ))
                discoveries.append({
                    "discovery_id": discovery_id,
                    "broker_symbol": name,
                    "mapped_instrument_ids": mapped_ids,
                    "mapping_conflict": len(mapped_ids) > 1,
                    "availability_status": availability,
                    "verification_status": "partially_verified" if mapped_ids else "unverified",
                    "metadata_sha256": fingerprint,
                })
        return {
            "account_observation_id": account_observation_id,
            "account_fingerprint": account_fingerprint,
            "account_identity_pinned": identity_pinned,
            "discovered_at": observed_at,
            "symbol_count": len(discoveries),
            "symbols": discoveries,
            "registry_version": REGISTRY_VERSION,
        }

    def latest_symbol(
        self, account_fingerprint: str, broker_symbol: str
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("""
                SELECT * FROM mt5_instrument_discoveries
                WHERE account_fingerprint = ? AND broker_symbol = ?
                ORDER BY discovered_at DESC, rowid DESC LIMIT 1
            """, (account_fingerprint, broker_symbol)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        result["mapped_instrument_ids"] = json.loads(
            result.pop("mapped_instrument_ids_json")
        )
        result["mapping_conflict"] = bool(result["mapping_conflict"])
        return result


    def record_verification(
        self,
        account_identity: dict[str, Any],
        instrument_id: str,
        broker_symbol: str,
        metadata_sha256: str,
        evidence_refs: list[dict[str, Any]],
        reviewer: str,
    ) -> dict[str, Any]:
        """Append an explicit operator approval backed by persisted canonical evidence."""
        if not isinstance(account_identity, dict) or account_identity.get("identity_pinned") is not True:
            raise ValueError("a pinned account identity is required for instrument verification")
        account_fingerprint = account_identity.get("account_fingerprint")
        if (
            not isinstance(account_fingerprint, str)
            or len(account_fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in account_fingerprint)
        ):
            raise ValueError("account_fingerprint must be a lowercase SHA-256 hex digest")
        if not isinstance(instrument_id, str) or not instrument_id.strip():
            raise ValueError("instrument_id must be a non-empty string")
        if not isinstance(broker_symbol, str) or not broker_symbol.strip():
            raise ValueError("broker_symbol must be a non-empty string")
        if (
            not isinstance(metadata_sha256, str)
            or len(metadata_sha256) != 64
            or any(char not in "0123456789abcdef" for char in metadata_sha256)
        ):
            raise ValueError("metadata_sha256 must be a lowercase SHA-256 hex digest")
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError("reviewer must be a non-empty string")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            raise ValueError("at least one canonical evidence reference is required")

        normalized_refs = []
        seen_reports = set()
        seen_timeframes = set()
        for item in evidence_refs:
            if not isinstance(item, dict):
                raise ValueError("each evidence reference must be an object")
            report_id = item.get("report_id")
            report_hash = item.get("report_sha256")
            timeframe = item.get("timeframe")
            candle_count = item.get("closed_candle_count")
            if not isinstance(report_id, str) or not report_id.strip():
                raise ValueError("evidence report_id must be a non-empty string")
            if (
                not isinstance(report_hash, str)
                or len(report_hash) != 64
                or any(char not in "0123456789abcdef" for char in report_hash)
            ):
                raise ValueError("evidence report_sha256 must be a lowercase SHA-256 hex digest")
            if timeframe not in {"5m", "15m", "1h", "4h"}:
                raise ValueError("evidence timeframe is unsupported")
            if isinstance(candle_count, bool) or not isinstance(candle_count, int) or candle_count < 1:
                raise ValueError("evidence must contain at least one valid closed canonical candle")
            if report_id in seen_reports or timeframe in seen_timeframes:
                raise ValueError("evidence report IDs and timeframes must be unique")
            seen_reports.add(report_id)
            seen_timeframes.add(timeframe)
            normalized_refs.append({
                "report_id": report_id,
                "report_sha256": report_hash,
                "timeframe": timeframe,
                "closed_candle_count": candle_count,
            })

        # Verification must be grounded in the latest account-scoped discovery
        # and in immutable canonical evidence persisted in this same evidence DB.
        with self._connect() as db:
            discovery = db.execute("""
                SELECT metadata_sha256, mapped_instrument_ids_json, mapping_conflict
                FROM mt5_instrument_discoveries
                WHERE account_fingerprint = ? AND broker_symbol = ?
                ORDER BY discovered_at DESC, rowid DESC LIMIT 1
            """, (account_fingerprint, broker_symbol)).fetchone()
            if discovery is None:
                raise ValueError("instrument verification requires a persisted account-scoped discovery")
            if discovery["metadata_sha256"] != metadata_sha256:
                raise ValueError("metadata fingerprint does not match latest instrument discovery")
            if discovery["mapping_conflict"]:
                raise ValueError("instrument discovery has an unresolved mapping conflict")
            if instrument_id not in json.loads(discovery["mapped_instrument_ids_json"]):
                raise ValueError("instrument ID is not mapped to the discovered broker symbol")

            for evidence in normalized_refs:
                report_row = db.execute("""
                    SELECT batch_id, instrument_id, broker, server_name,
                           account_environment, broker_symbol, timeframe,
                           report_json, report_sha256
                    FROM canonical_validation_reports
                    WHERE report_id = ?
                """, (evidence["report_id"],)).fetchone()
                if report_row is None:
                    raise ValueError("canonical evidence report does not exist")
                report_json = report_row["report_json"]
                actual_hash = hashlib.sha256(report_json.encode("utf-8")).hexdigest()
                if actual_hash != report_row["report_sha256"]:
                    raise ValueError("stored canonical evidence report hash mismatch")
                if actual_hash != evidence["report_sha256"]:
                    raise ValueError("evidence report hash does not match persisted canonical report")

                expected_identity = (
                    instrument_id, report_row["broker"], account_identity.get("server"),
                    account_identity.get("environment"), broker_symbol, evidence["timeframe"],
                )
                actual_identity = (
                    report_row["instrument_id"], report_row["broker"], report_row["server_name"],
                    report_row["account_environment"], report_row["broker_symbol"], report_row["timeframe"],
                )
                if actual_identity != expected_identity:
                    raise ValueError("canonical evidence report identity does not match instrument verification")

                try:
                    persisted_report = json.loads(report_json)
                except (TypeError, json.JSONDecodeError) as exc:
                    raise ValueError("canonical evidence report JSON is invalid") from exc
                quality = persisted_report.get("report")
                history = quality.get("history_completeness") if isinstance(quality, dict) else None
                if isinstance(quality, dict) and quality.get("account_fingerprint") != account_fingerprint:
                    raise ValueError("canonical evidence report account identity does not match pinned account")
                if (
                    persisted_report.get("report_id") != evidence["report_id"]
                    or persisted_report.get("instrument_id") != instrument_id
                    or persisted_report.get("server_name") != account_identity.get("server")
                    or persisted_report.get("account_environment") != account_identity.get("environment")
                    or persisted_report.get("broker_symbol") != broker_symbol
                    or persisted_report.get("timeframe") != evidence["timeframe"]
                    or not isinstance(quality, dict)
                    or quality.get("account_fingerprint") != account_fingerprint
                    or quality.get("account_identity_pinned") is not True
                    or quality.get("instrument_metadata_sha256") != metadata_sha256
                    or not isinstance(history, dict)
                    or history.get("status") != "complete"
                    or history.get("account_fingerprint") != account_fingerprint
                    or history.get("instrument_id") != instrument_id
                    or history.get("broker_symbol") != broker_symbol
                    or history.get("timeframe") != evidence["timeframe"]
                ):
                    raise ValueError("canonical evidence report payload identity or metadata proof mismatch")

                candle_rows = db.execute("""
                    SELECT c.candle_id, r.account_fingerprint
                    FROM canonical_candles AS c
                    JOIN canonical_candle_sources AS s ON s.candle_id = c.candle_id
                    JOIN raw_observations AS r ON r.observation_id = s.observation_id
                    WHERE c.validation_report_id = ?
                      AND c.bar_status = 'closed'
                      AND c.validation_status = 'valid'
                      AND c.instrument_id = ?
                      AND c.broker = ?
                      AND c.server_name = ?
                      AND c.account_environment = ?
                      AND c.broker_symbol = ?
                      AND c.timeframe = ?
                """, (
                    evidence["report_id"], instrument_id, report_row["broker"],
                    account_identity.get("server"), account_identity.get("environment"),
                    broker_symbol, evidence["timeframe"],
                )).fetchall()
                actual_count = db.execute("""
                    SELECT COUNT(*) FROM canonical_candles
                    WHERE validation_report_id = ?
                      AND bar_status = 'closed'
                      AND validation_status = 'valid'
                """, (evidence["report_id"],)).fetchone()[0]
                if actual_count < 1:
                    raise ValueError("canonical evidence must contain at least one valid closed candle")
                if actual_count != evidence["closed_candle_count"]:
                    raise ValueError("evidence closed candle count does not match persisted canonical candles")
                if not candle_rows or any(
                    row["account_fingerprint"] != account_fingerprint for row in candle_rows
                ):
                    raise ValueError("canonical evidence source account identity does not match pinned account")
                distinct_candles = {row["candle_id"] for row in candle_rows}
                if len(distinct_candles) != actual_count:
                    raise ValueError("canonical evidence candles do not match requested instrument identity")

        verified_timeframes = sorted(seen_timeframes)
        verified_at = datetime.now(timezone.utc).isoformat()
        verification_id = str(uuid.uuid4())
        with self._connect() as db:
            db.execute("""
                INSERT INTO mt5_instrument_verifications (
                    verification_id, account_fingerprint, instrument_id,
                    broker_symbol, metadata_sha256, verified_timeframes_json,
                    evidence_refs_json, reviewer, verification_status,
                    verified_at, registry_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                verification_id, account_fingerprint, instrument_id,
                broker_symbol, metadata_sha256, _json(verified_timeframes),
                _json(normalized_refs), reviewer.strip(), "verified",
                verified_at, REGISTRY_VERSION,
            ))
        return {
            "verification_id": verification_id,
            "account_fingerprint": account_fingerprint,
            "instrument_id": instrument_id,
            "broker_symbol": broker_symbol,
            "metadata_sha256": metadata_sha256,
            "verified_timeframes": verified_timeframes,
            "evidence_refs": normalized_refs,
            "reviewer": reviewer.strip(),
            "verification_status": "verified",
            "verified_at": verified_at,
            "registry_version": REGISTRY_VERSION,
        }

    def latest_verification(
        self,
        account_fingerprint: str,
        instrument_id: str,
        broker_symbol: str,
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("""
                SELECT * FROM mt5_instrument_verifications
                WHERE account_fingerprint = ?
                  AND instrument_id = ?
                  AND broker_symbol = ?
                ORDER BY verified_at DESC, rowid DESC LIMIT 1
            """, (account_fingerprint, instrument_id, broker_symbol)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["verified_timeframes"] = json.loads(result.pop("verified_timeframes_json"))
        result["evidence_refs"] = json.loads(result.pop("evidence_refs_json"))
        return result

    def instrument_verification_count(self) -> int:
        with self._connect() as db:
            return int(db.execute(
                "SELECT COUNT(*) FROM mt5_instrument_verifications"
            ).fetchone()[0])

    def account_observation_count(self) -> int:
        with self._connect() as db:
            return int(db.execute(
                "SELECT COUNT(*) FROM mt5_account_observations"
            ).fetchone()[0])

    def instrument_discovery_count(self) -> int:
        with self._connect() as db:
            return int(db.execute(
                "SELECT COUNT(*) FROM mt5_instrument_discoveries"
            ).fetchone()[0])
