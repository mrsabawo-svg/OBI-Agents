import hashlib
import json
import sqlite3

import pytest

from core.instrument_registry import (
    RegistryValidationError,
    validate_instrument_record,
)
from core.raw_observation_store import RawObservationStore


def valid_record():
    return {
        "instrument_id": "synthetic.sfx_vol_75",
        "family_id": "SFX_VOL",
        "display_name": "SFX Vol 75",
        "broker": "Weltrade",
        "broker_symbol": None,
        "account_environment": "unknown",
        "platform": "MT5",
        "availability_status": "unverified",
        "source_type": "unknown",
        "verification_status": "unverified",
        "supported_timeframes": [],
        "registry_version": "0.1.0",
    }


def source_envelope():
    return {
        "instrument_id": "synthetic.sfx_vol_75",
        "broker": "Weltrade",
        "server_name": "Weltrade-Demo",
        "account_environment": "demo",
        "platform": "MT5",
        "broker_symbol": "SFX Vol 75",
        "timeframe": "5m",
        "source_type": "broker_bars",
        "retrieved_at": "2026-11-08T10:00:00+00:00",
        "batch_id": "batch-test-001",
    }


def test_registry_allows_unverified_symbol_to_remain_unknown():
    record = valid_record()
    assert validate_instrument_record(record) is record
    assert record["broker_symbol"] is None


def test_registry_rejects_unknown_family():
    record = valid_record()
    record["family_id"] = "MADE_UP"
    with pytest.raises(RegistryValidationError):
        validate_instrument_record(record)


def test_registry_verified_requires_exact_symbol_and_timeframe():
    record = valid_record()
    record.update({
        "verification_status": "verified",
        "availability_status": "data_verified",
        "supported_timeframes": ["5m"],
    })
    with pytest.raises(RegistryValidationError):
        validate_instrument_record(record)


def test_registry_verified_record_requires_evidence_fields():
    record = valid_record()
    record.update({
        "broker_symbol": "SFX Vol 75",
        "verification_status": "verified",
        "availability_status": "data_verified",
        "supported_timeframes": ["5m"],
        "account_environment": "demo",
        "evidence_refs": ["evidence://account-observation-001"],
        "last_verified_at": "2026-11-08T10:00:00Z",
    })
    assert validate_instrument_record(record) is record


def test_raw_store_preserves_payload_and_hash(tmp_path):
    store = RawObservationStore(tmp_path / "raw.sqlite3")
    payload = {"time": 1794132000, "open": 100.0, "tick_volume": 42}
    result = store.append_batch(source_envelope(), [payload])

    assert result["persisted"] is True
    assert result["inserted_count"] == 1
    saved = store.get(result["observation_ids"][0])
    expected = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert saved["payload"] == payload
    assert saved["payload_sha256"] == hashlib.sha256(expected.encode()).hexdigest()
    assert saved["source_type"] == "broker_bars"


def test_raw_store_retains_duplicate_and_conflicting_observations(tmp_path):
    store = RawObservationStore(tmp_path / "raw.sqlite3")
    first = {"time": 1794132000, "open": 100.0, "close": 101.0}
    conflict = {"time": 1794132000, "open": 100.0, "close": 99.0}
    result = store.append_batch(source_envelope(), [first, first, conflict])

    assert result["inserted_count"] == 3
    assert store.count() == 3
    assert len(set(result["observation_ids"])) == 3


def test_raw_store_rejects_update_and_delete(tmp_path):
    path = tmp_path / "raw.sqlite3"
    store = RawObservationStore(path)
    result = store.append_batch(source_envelope(), [{"time": 1794132000, "open": 1}])
    observation_id = result["observation_ids"][0]

    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE raw_observations SET broker_symbol = ? WHERE observation_id = ?",
                ("tampered", observation_id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "DELETE FROM raw_observations WHERE observation_id = ?",
                (observation_id,),
            )


def test_raw_store_requires_source_identity(tmp_path):
    store = RawObservationStore(tmp_path / "raw.sqlite3")
    with pytest.raises(ValueError, match="broker_symbol"):
        store.append_batch({"instrument_id": "x"}, [{"time": 1}])


def test_raw_store_persists_row_level_quarantine_with_source_link(tmp_path):
    store = RawObservationStore(tmp_path / "raw.sqlite3")
    observations = [
        {"time": 1794132000, "open": 100, "high": 102, "low": 99, "close": 101},
        {"time": 1794132300, "open": 100, "high": 98, "low": 99, "close": 101},
    ]
    result = store.append_batch(
        source_envelope(),
        observations,
        quarantine_records=[{
            "observation_index": 1,
            "reason_code": "INVALID_OHLC",
            "reason_detail": "high below close",
            "validator_version": "1",
        }],
    )

    assert result["inserted_count"] == 2
    assert result["quarantine_count"] == 1
    record = store.get_quarantine_records()[0]
    assert record["observation_id"] == result["observation_ids"][1]
    assert record["batch_id"] == result["batch_id"]
    assert record["reason_code"] == "INVALID_OHLC"
    assert store.quarantine_count() == 1


def test_quarantine_records_are_append_only(tmp_path):
    path = tmp_path / "raw.sqlite3"
    store = RawObservationStore(path)
    result = store.append_batch(
        source_envelope(),
        [{"time": 1794132000, "open": 1}],
        quarantine_records=[{
            "observation_index": 0,
            "reason_code": "INVALID_OHLC",
        }],
    )
    quarantine_id = store.get_quarantine_records()[0]["quarantine_id"]

    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE quarantine_records SET reason_code = ? WHERE quarantine_id = ?",
                ("INVALID_TIMESTAMP", quarantine_id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "DELETE FROM quarantine_records WHERE quarantine_id = ?",
                (quarantine_id,),
            )


def test_invalid_quarantine_record_is_rejected_before_raw_insert(tmp_path):
    store = RawObservationStore(tmp_path / "raw.sqlite3")
    with pytest.raises(ValueError, match="unknown quarantine reason_code"):
        store.append_batch(
            source_envelope(),
            [{"time": 1794132000, "open": 1}],
            quarantine_records=[{
                "observation_index": 0,
                "reason_code": "MADE_UP_REASON",
            }],
        )
    assert store.count() == 0
    assert store.quarantine_count() == 0


def test_non_finite_raw_values_are_preserved_as_explicit_json_sentinels(tmp_path):
    store = RawObservationStore(tmp_path / "raw.sqlite3")
    result = store.append_batch(
        source_envelope(),
        [{"time": 1794132000, "open": float("nan")}],
    )
    saved = store.get(result["observation_ids"][0])
    assert saved["payload"]["open"] == {"__non_finite_float__": "nan"}
