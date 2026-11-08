import sqlite3

import pytest

from core.canonical_data_store import (
    CanonicalCandleStore,
    CanonicalDataError,
    validate_canonical_candle,
)
from core.raw_observation_store import RawObservationStore


def envelope():
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
        "batch_id": "raw-batch-001",
    }


def validation_report(timeframe="5m", report_id="quality-report-001", batch_id="raw-batch-001"):
    return {
        "report_id": report_id,
        "batch_id": batch_id,
        "instrument_id": "synthetic.sfx_vol_75",
        "broker": "Weltrade",
        "server_name": "Weltrade-Demo",
        "account_environment": "demo",
        "broker_symbol": "SFX Vol 75",
        "timeframe": timeframe,
        "validator_version": "1",
        "report": {
            "received_count": 1,
            "quarantined_observation_count": 0,
            "quarantine_record_count": 0,
            "reason_counts": {},
        },
    }


def candle(source_id):
    return {
        "instrument_id": "synthetic.sfx_vol_75",
        "broker": "Weltrade",
        "server_name": "Weltrade-Demo",
        "account_environment": "demo",
        "broker_symbol": "SFX Vol 75",
        "timeframe": "5m",
        "open_time": "2026-11-08T10:00:00Z",
        "close_time": "2026-11-08T10:05:00Z",
        "open": 100.0,
        "high": 103.0,
        "low": 99.0,
        "close": 102.0,
        "volume": 25,
        "volume_type": "tick_volume",
        "source_class": "broker_bar",
        "bar_status": "closed",
        "source_observation_ids": [source_id],
        "transformation_version": "broker-bars-v1",
        "validation_report_id": "quality-report-001",
        "validation_status": "valid",
    }


def setup_store(tmp_path):
    path = tmp_path / "market.sqlite3"
    raw = RawObservationStore(path)
    result = raw.append_batch(envelope(), [{
        "time": 1794132000,
        "open": 100.0,
        "high": 103.0,
        "low": 99.0,
        "close": 102.0,
        "tick_volume": 25,
    }])
    return path, result["observation_ids"][0]


def test_canonical_candle_persists_with_exact_raw_lineage(tmp_path):
    path, source_id = setup_store(tmp_path)
    store = CanonicalCandleStore(path)

    result = store.append_batch([candle(source_id)], validation_report())

    assert result["inserted_count"] == 1
    saved = store.get(result["candle_ids"][0])
    assert saved["source_observation_ids"] == [source_id]
    assert saved["source_class"] == "broker_bar"
    assert saved["volume_type"] == "tick_volume"
    assert saved["bar_status"] == "closed"
    assert saved["validation_report_id"] == "quality-report-001"
    assert saved["validation_status"] == "valid"
    assert len(saved["record_sha256"]) == 64
    assert result["record_sha256"] == [saved["record_sha256"]]


def test_canonical_store_rejects_missing_source_observation(tmp_path):
    path, _ = setup_store(tmp_path)
    store = CanonicalCandleStore(path)

    with pytest.raises(CanonicalDataError, match="do not exist"):
        store.append_batch([candle("missing-observation")], validation_report())

    assert store.count() == 0


def test_canonical_store_rejects_source_identity_mismatch(tmp_path):
    path, source_id = setup_store(tmp_path)
    store = CanonicalCandleStore(path)
    record = candle(source_id)
    record["broker_symbol"] = "A_DIFFERENT_SYMBOL"

    with pytest.raises(CanonicalDataError, match="identity"):
        store.append_batch([record], validation_report())

    assert store.count() == 0


def test_broker_bar_requires_matching_source_timeframe(tmp_path):
    path, source_id = setup_store(tmp_path)
    store = CanonicalCandleStore(path)
    record = candle(source_id)
    record["timeframe"] = "15m"

    with pytest.raises(CanonicalDataError, match="timeframe"):
        store.append_batch([record], validation_report(timeframe="15m"))


def test_canonical_candle_rejects_naive_timestamps():
    record = candle("source")
    record["open_time"] = "2026-11-08T10:00:00"

    with pytest.raises(CanonicalDataError, match="timezone"):
        validate_canonical_candle(record)


def test_canonical_candle_rejects_non_finite_and_invalid_ohlc():
    record = candle("source")
    record["high"] = float("nan")

    with pytest.raises(CanonicalDataError, match="finite"):
        validate_canonical_candle(record)

    record = candle("source")
    record["high"] = 101.0

    with pytest.raises(CanonicalDataError, match="OHLC"):
        validate_canonical_candle(record)


def test_canonical_records_and_source_links_are_append_only(tmp_path):
    path, source_id = setup_store(tmp_path)
    store = CanonicalCandleStore(path)
    result = store.append_batch([candle(source_id)], validation_report())
    candle_id = result["candle_ids"][0]

    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE canonical_candles SET close = 999 WHERE candle_id = ?",
                (candle_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "DELETE FROM canonical_candle_sources WHERE candle_id = ?",
                (candle_id,),
            )



def test_canonical_batch_is_atomic_when_later_record_is_invalid(tmp_path):
    path, source_id = setup_store(tmp_path)
    store = CanonicalCandleStore(path)
    valid = candle(source_id)
    invalid_source = candle(source_id)
    invalid_source["source_observation_ids"] = ["not-in-the-raw-store"]

    with pytest.raises(CanonicalDataError, match="do not exist"):
        store.append_batch([valid, invalid_source], validation_report())

    assert store.count() == 0


def test_canonical_store_rejects_source_class_mismatch(tmp_path):
    path = tmp_path / "market.sqlite3"
    source_envelope = envelope()
    source_envelope["source_type"] = "third_party"
    raw = RawObservationStore(path)
    result = raw.append_batch(source_envelope, [{"time": 1794132000, "close": 102}])
    store = CanonicalCandleStore(path)

    with pytest.raises(CanonicalDataError, match="source type"):
        store.append_batch([candle(result["observation_ids"][0])], validation_report(batch_id=result["batch_id"]))

    assert store.count() == 0


def test_canonical_candle_rejects_timeframe_boundary_mismatch():
    record = candle("source")
    record["close_time"] = "2026-11-08T10:15:00Z"

    with pytest.raises(CanonicalDataError, match="timeframe"):
        validate_canonical_candle(record)


def test_canonical_hash_is_stable_across_equivalent_timezone_and_numeric_inputs(tmp_path):
    path, source_id = setup_store(tmp_path)
    store = CanonicalCandleStore(path)
    utc_record = candle(source_id)
    offset_record = candle(source_id)
    offset_record["open_time"] = "2026-11-08T12:00:00+02:00"
    offset_record["close_time"] = "2026-11-08T12:05:00+02:00"
    offset_record["volume"] = 25.0

    result = store.append_batch([utc_record, offset_record], validation_report())

    assert result["record_sha256"][0] == result["record_sha256"][1]


def test_canonical_candle_rejects_unknown_validation_status():
    record = candle("source")
    record["validation_status"] = "rejected"

    with pytest.raises(CanonicalDataError, match="validation_status"):
        validate_canonical_candle(record)
