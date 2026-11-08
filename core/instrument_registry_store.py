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


REGISTRY_VERSION = "obi-instrument-registry-v1"


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
