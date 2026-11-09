from datetime import datetime, timedelta

import pytest

from agents.archive_agent import ArchiveAgent
from agents.signal_reviewer import SignalReviewer, SAST
from core.signal_identity import (
    SETUP_IDENTITY_VERSION,
    build_setup_identity,
    create_signal_identity,
    resolve_setup_identity,
)
from main import build_signal_id


@pytest.fixture(autouse=True)
def duplicate_review_uses_fresh_test_snapshot(monkeypatch):
    monkeypatch.setattr("agents.signal_reviewer.memory_store.load_with_status", lambda: ({}, False))


def payload(signal_id="SIGNAL_001"):
    return {
        "id": signal_id,
        "symbol": "XAUUSD",
        "trigger": {
            "direction": "BUY",
            "entry": 4000.0,
            "sl": 3990.0,
            "tp1": 4010.0,
            "tp2": 4020.0,
            "tp3": 4030.0,
            "tags": ["OB", "FVG"],
        },
        "regime": {"label": "TRENDING"},
    }


def opened(hours_ago):
    return (datetime.now(SAST) - timedelta(hours=hours_ago)).strftime(
        "%Y-%m-%d %H:%M SAST"
    )


def test_signal_event_id_is_unique_even_inside_the_same_second():
    first = build_signal_id("XAUUSD")
    second = build_signal_id("XAUUSD")
    assert first != second
    assert first.startswith("XAUUSD_")
    assert len(first.rsplit("_", 1)[-1]) == 32


def test_signal_identity_and_setup_identity_are_distinct():
    first = payload("SIGNAL_001")
    second = payload("SIGNAL_002")
    assert first["id"] != second["id"]
    assert build_setup_identity(first) == build_setup_identity(second)


def test_setup_identity_is_stable_across_equivalent_numeric_representations():
    first = payload()
    second = payload("SIGNAL_002")
    second["trigger"]["entry"] = "4000.000"
    second["trigger"]["sl"] = "3990.00"
    assert build_setup_identity(first) == build_setup_identity(second)


def test_material_setup_change_gets_new_setup_identity():
    first = payload()
    second = payload("SIGNAL_002")
    second["trigger"]["tp2"] = 4025.0
    assert build_setup_identity(first) != build_setup_identity(second)


def test_archive_persists_both_signal_and_setup_identity():
    signal = payload()
    identity = create_signal_identity(signal["symbol"], signal["trigger"], signal["regime"])
    signal["id"] = identity["signal_id"]
    signal["signal_identity"] = identity
    entry = ArchiveAgent()._build_entry(signal)
    assert entry["id"] == signal["id"]
    assert entry["setup_identity_version"] == SETUP_IDENTITY_VERSION
    assert entry["setup_id"] == identity["setup_id"]


def test_identity_authority_issues_distinct_events_for_same_setup():
    first = create_signal_identity("XAUUSD", payload()["trigger"], payload()["regime"])
    second = create_signal_identity("XAUUSD", payload()["trigger"], payload()["regime"])
    assert first["signal_id"] != second["signal_id"]
    assert first["setup_id"] == second["setup_id"]
    assert first["setup_identity_version"] == SETUP_IDENTITY_VERSION


def test_archive_does_not_fabricate_missing_event_or_setup_identity():
    entry = ArchiveAgent()._build_entry({"symbol": "EURUSD"})
    assert entry["id"] is None
    assert entry["setup_id"] is None
    assert entry["setup_identity_version"] is None
    assert entry["setup_identity_status"] == "unavailable"


def test_resolver_rejects_malformed_issued_identity_instead_of_recomputing():
    candidate = payload()
    candidate["signal_identity"] = {
        "signal_id": candidate["id"],
        "setup_id": "not-a-digest",
        "setup_identity_version": SETUP_IDENTITY_VERSION,
    }
    with pytest.raises(ValueError, match="invalid"):
        resolve_setup_identity(candidate)



def test_resolver_rejects_well_formed_but_stale_setup_digest():
    candidate = payload()
    candidate["signal_identity"] = {
        "signal_id": candidate["id"],
        "setup_id": "a" * 64,
        "setup_identity_version": SETUP_IDENTITY_VERSION,
    }
    with pytest.raises(ValueError, match="invalid"):
        resolve_setup_identity(candidate)



def test_archive_log_rejects_missing_event_identity_without_writing(monkeypatch):
    memory = {"_archive": []}
    saved = []
    monkeypatch.setattr("agents.archive_agent.load_memory", lambda: memory)
    monkeypatch.setattr("agents.archive_agent.save_memory", lambda value: saved.append(value) or True)
    assert not ArchiveAgent().log({"symbol": "EURUSD"})
    assert memory["_archive"] == []
    assert saved == []


def test_exact_signal_id_is_duplicate_even_if_open_timestamp_is_unavailable(monkeypatch):
    candidate = payload("SIGNAL_001")
    archive = [{
        "id": "SIGNAL_001",
        "symbol": "XAUUSD",
        "status": "OPEN",
        "setup_id": "different-setup",
    }]
    monkeypatch.setattr("agents.signal_reviewer.memory_store.load_with_status", lambda: ({"_archive": archive}, False))
    assert SignalReviewer("XAUUSD").is_duplicate(candidate)



def test_exact_signal_id_remains_duplicate_after_archive_closure(monkeypatch):
    candidate = payload("SIGNAL_001")
    archive = [{
        "id": "SIGNAL_001",
        "symbol": "XAUUSD",
        "status": "CLOSED",
        "setup_identity_version": SETUP_IDENTITY_VERSION,
        "setup_id": build_setup_identity(candidate),
    }]
    monkeypatch.setattr("agents.signal_reviewer.memory_store.load_with_status", lambda: ({"_archive": archive}, False))
    assert SignalReviewer("XAUUSD").is_duplicate(candidate)


def test_same_setup_is_duplicate_even_with_a_new_signal_id(monkeypatch):
    candidate = payload("SIGNAL_002")
    existing = payload("SIGNAL_001")
    archive = [{
        "id": existing["id"],
        "symbol": "XAUUSD",
        "status": "OPEN",
        "opened": opened(1),
        "direction": existing["trigger"]["direction"],
        "entry": existing["trigger"]["entry"],
        "sl": existing["trigger"]["sl"],
        "tp1": existing["trigger"]["tp1"],
        "tp2": existing["trigger"]["tp2"],
        "tp3": existing["trigger"]["tp3"],
        "tags": existing["trigger"]["tags"],
        "regime": existing["regime"]["label"],
        "setup_identity_version": SETUP_IDENTITY_VERSION,
        "setup_id": build_setup_identity(existing),
    }]
    monkeypatch.setattr("agents.signal_reviewer.memory_store.load_with_status", lambda: ({"_archive": archive}, False))
    assert SignalReviewer("XAUUSD").is_duplicate(candidate)


def test_same_entry_with_materially_different_risk_is_not_a_duplicate(monkeypatch):
    candidate = payload("SIGNAL_002")
    existing = payload("SIGNAL_001")
    existing["trigger"]["sl"] = 3980.0
    archive = [{
        "id": existing["id"],
        "symbol": "XAUUSD",
        "status": "OPEN",
        "opened": opened(1),
        "direction": existing["trigger"]["direction"],
        "entry": existing["trigger"]["entry"],
        "sl": existing["trigger"]["sl"],
        "tp1": existing["trigger"]["tp1"],
        "tp2": existing["trigger"]["tp2"],
        "tp3": existing["trigger"]["tp3"],
        "tags": existing["trigger"]["tags"],
        "regime": existing["regime"]["label"],
        "setup_identity_version": SETUP_IDENTITY_VERSION,
        "setup_id": build_setup_identity(existing),
        "entry": 4000.0,
    }]
    monkeypatch.setattr("agents.signal_reviewer.memory_store.load_with_status", lambda: ({"_archive": archive}, False))
    assert not SignalReviewer("XAUUSD").is_duplicate(candidate)


def test_old_setup_is_not_blocked_after_duplicate_window(monkeypatch):
    candidate = payload("SIGNAL_002")
    existing = payload("SIGNAL_001")
    archive = [{
        "id": existing["id"],
        "symbol": "XAUUSD",
        "status": "OPEN",
        "opened": opened(3),
        "setup_identity_version": SETUP_IDENTITY_VERSION,
        "setup_id": build_setup_identity(existing),
    }]
    monkeypatch.setattr("agents.signal_reviewer.memory_store.load_with_status", lambda: ({"_archive": archive}, False))
    assert not SignalReviewer("XAUUSD").is_duplicate(candidate)


def test_legacy_archive_without_setup_id_is_reconstructed(monkeypatch):
    candidate = payload("SIGNAL_002")
    archive = [{
        "id": "SIGNAL_001",
        "symbol": "XAUUSD",
        "status": "OPEN",
        "opened": opened(1),
        "direction": "BUY",
        "entry": 4000.0,
        "sl": 3990.0,
        "tp1": 4010.0,
        "tp2": 4020.0,
        "tp3": 4030.0,
        "tags": ["OB", "FVG"],
        "regime": "TRENDING",
    }]
    monkeypatch.setattr("agents.signal_reviewer.memory_store.load_with_status", lambda: ({"_archive": archive}, False))
    assert SignalReviewer("XAUUSD").is_duplicate(candidate)



def test_incomplete_legacy_archive_payload_is_preserved_without_fabricated_setup_identity():
    entry = ArchiveAgent()._build_entry({"id": "legacy-minimal", "symbol": "EURUSD"})
    assert entry["id"] == "legacy-minimal"
    assert entry["setup_identity_status"] == "unavailable"
    assert entry["setup_identity_version"] is None
    assert entry["setup_id"] is None


def test_setup_identity_rejects_non_finite_price_fields():
    candidate = payload()
    candidate["trigger"]["entry"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        build_setup_identity(candidate)
