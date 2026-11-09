import sqlite3

import pytest

from core.instrument_registry_store import InstrumentRegistryStore
from core.raw_observation_store import RawObservationStore
from core.canonical_data_store import CanonicalCandleStore


def _identity(pinned=True, fingerprint="a" * 64):
    return {
        "account_fingerprint": fingerprint,
        "identity_pinned": pinned,
        "broker_company": "Weltrade Ltd",
        "server": "Weltrade-Demo",
        "environment": "demo",
        "platform": "MT5",
        "account_login_suffix": "3456",
    }


def _evidence(report_id="report-5m", timeframe="5m"):
    return {
        "report_id": report_id,
        "report_sha256": "b" * 64,
        "timeframe": timeframe,
        "closed_candle_count": 299,
    }


def _discovery(store, identity=None, symbol_map=None):
    return store.record_discovery(
        identity or _identity(),
        [{
            "name": "SFX Vol 75",
            "description": "SFX Vol 75",
            "selected": True,
            "visible": True,
            "metadata": {"name": "SFX Vol 75", "trade_contract_size": 1.0},
        }],
        symbol_map or {"SFX_VOL_75": "SFX Vol 75"},
    )


def _persisted_evidence(path, identity, timeframe="5m", report_id="report-5m"):
    duration = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}[timeframe]
    open_epoch = 1794132000
    from datetime import datetime, timezone, timedelta

    open_time = datetime.fromtimestamp(open_epoch, timezone.utc)
    close_time = open_time + timedelta(seconds=duration)
    raw = RawObservationStore(path)
    source = raw.append_batch({
        "instrument_id": "SFX_VOL_75",
        "broker": "Weltrade",
        "server_name": identity["server"],
        "account_fingerprint": identity["account_fingerprint"],
        "account_environment": identity["environment"],
        "platform": "MT5",
        "broker_symbol": "SFX Vol 75",
        "timeframe": timeframe,
        "source_type": "broker_bars",
        "retrieved_at": "2026-11-09T10:00:00+00:00",
        "batch_id": "batch-" + report_id,
    }, [{
        "time": open_epoch,
        "open": 100.0,
        "high": 103.0,
        "low": 99.0,
        "close": 102.0,
        "tick_volume": 25,
    }])
    report = {
        "report_id": report_id,
        "batch_id": source["batch_id"],
        "instrument_id": "SFX_VOL_75",
        "broker": "Weltrade",
        "server_name": identity["server"],
        "account_environment": identity["environment"],
        "broker_symbol": "SFX Vol 75",
        "timeframe": timeframe,
        "validator_version": "1",
        "report": {"received_count": 1, "quarantined_observation_count": 0},
    }
    candle = {
        "instrument_id": "SFX_VOL_75",
        "broker": "Weltrade",
        "server_name": identity["server"],
        "account_environment": identity["environment"],
        "broker_symbol": "SFX Vol 75",
        "timeframe": timeframe,
        "open_time": open_time.isoformat(),
        "close_time": close_time.isoformat(),
        "open": 100.0,
        "high": 103.0,
        "low": 99.0,
        "close": 102.0,
        "volume": 25,
        "volume_type": "tick_volume",
        "source_class": "broker_bar",
        "bar_status": "closed",
        "source_observation_ids": source["observation_ids"],
        "transformation_version": "broker-bars-v1",
        "validation_report_id": report_id,
        "validation_status": "valid",
    }
    result = CanonicalCandleStore(path).append_batch([candle], report)
    return {
        "report_id": report_id,
        "report_sha256": result["validation_report_sha256"],
        "timeframe": timeframe,
        "closed_candle_count": 1,
    }


def test_explicit_instrument_verification_is_account_scoped_and_append_only(tmp_path):
    path = tmp_path / "registry.sqlite3"
    store = InstrumentRegistryStore(path)
    discovery = _discovery(store)
    metadata_sha = discovery["symbols"][0]["metadata_sha256"]
    evidence = _persisted_evidence(path, _identity())

    verification = store.record_verification(
        _identity(),
        "SFX_VOL_75",
        "SFX Vol 75",
        metadata_sha,
        [evidence],
        "operator-review",
    )

    assert verification["verification_status"] == "verified"
    assert verification["verified_timeframes"] == ["5m"]
    assert verification["evidence_refs"][0]["report_sha256"] == evidence["report_sha256"]
    latest = store.latest_verification("a" * 64, "SFX_VOL_75", "SFX Vol 75")
    assert latest["verification_id"] == verification["verification_id"]
    assert store.latest_verification("c" * 64, "SFX_VOL_75", "SFX Vol 75") is None
    assert store.instrument_verification_count() == 1

    with sqlite3.connect(path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute(
                "UPDATE mt5_instrument_verifications SET reviewer = ? WHERE verification_id = ?",
                ("tampered", verification["verification_id"]),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute(
                "DELETE FROM mt5_instrument_verifications WHERE verification_id = ?",
                (verification["verification_id"],),
            )


def test_instrument_verification_rejects_unpersisted_or_mismatched_evidence(tmp_path):
    path = tmp_path / "evidence.sqlite3"
    store = InstrumentRegistryStore(path)
    discovery = _discovery(store)
    metadata_sha = discovery["symbols"][0]["metadata_sha256"]
    evidence = _persisted_evidence(path, _identity())

    fabricated = dict(evidence, report_id="fabricated-report")
    with pytest.raises(ValueError, match="does not exist"):
        store.record_verification(
            _identity(), "SFX_VOL_75", "SFX Vol 75", metadata_sha,
            [fabricated], "operator-review",
        )

    wrong_hash = dict(evidence, report_sha256="b" * 64)
    with pytest.raises(ValueError, match="hash"):
        store.record_verification(
            _identity(), "SFX_VOL_75", "SFX Vol 75", metadata_sha,
            [wrong_hash], "operator-review",
        )

    wrong_count = dict(evidence, closed_candle_count=2)
    with pytest.raises(ValueError, match="count"):
        store.record_verification(
            _identity(), "SFX_VOL_75", "SFX Vol 75", metadata_sha,
            [wrong_count], "operator-review",
        )

    assert store.instrument_verification_count() == 0


def test_instrument_verification_rejects_canonical_sources_from_another_account(tmp_path):
    path = tmp_path / "evidence.sqlite3"
    store = InstrumentRegistryStore(path)
    discovery_a = _discovery(store, _identity(fingerprint="a" * 64))
    evidence = _persisted_evidence(path, _identity(fingerprint="a" * 64))
    discovery_b = _discovery(store, _identity(fingerprint="c" * 64))

    with pytest.raises(ValueError, match="account identity"):
        store.record_verification(
            _identity(fingerprint="c" * 64), "SFX_VOL_75", "SFX Vol 75",
            discovery_b["symbols"][0]["metadata_sha256"], [evidence],
            "operator-review",
        )

    assert store.instrument_verification_count() == 0


def test_instrument_verification_requires_pinned_account_and_persisted_evidence_shape(tmp_path):
    store = InstrumentRegistryStore(tmp_path / "registry.sqlite3")
    discovery = _discovery(store)
    metadata_sha = discovery["symbols"][0]["metadata_sha256"]

    with pytest.raises(ValueError, match="pinned account"):
        store.record_verification(
            _identity(pinned=False),
            "SFX_VOL_75",
            "SFX Vol 75",
            metadata_sha,
            [_evidence()],
            "operator-review",
        )

    with pytest.raises(ValueError, match="unique"):
        store.record_verification(
            _identity(),
            "SFX_VOL_75",
            "SFX Vol 75",
            metadata_sha,
            [_evidence("report-a", "5m"), _evidence("report-b", "5m")],
            "operator-review",
        )

    with pytest.raises(ValueError, match="closed canonical candle"):
        bad = _evidence()
        bad["closed_candle_count"] = 0
        store.record_verification(
            _identity(),
            "SFX_VOL_75",
            "SFX Vol 75",
            metadata_sha,
            [bad],
            "operator-review",
        )
