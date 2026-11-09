import sqlite3

import pytest

from core.instrument_registry_store import InstrumentRegistryStore


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


def test_explicit_instrument_verification_is_account_scoped_and_append_only(tmp_path):
    path = tmp_path / "registry.sqlite3"
    store = InstrumentRegistryStore(path)
    discovery = _discovery(store)
    metadata_sha = discovery["symbols"][0]["metadata_sha256"]

    verification = store.record_verification(
        _identity(),
        "SFX_VOL_75",
        "SFX Vol 75",
        metadata_sha,
        [_evidence()],
        "operator-review",
    )

    assert verification["verification_status"] == "verified"
    assert verification["verified_timeframes"] == ["5m"]
    assert verification["evidence_refs"][0]["report_sha256"] == "b" * 64
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
