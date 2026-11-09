from datetime import datetime, timedelta

import pytest

from agents.archive_agent import ArchiveAgent
from agents.signal_reviewer import SignalReviewer, SAST
from core.signal_identity import build_setup_identity
from main import build_signal_id


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
    assert len(first.rsplit("_", 1)[-1]) == 8


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
    entry = ArchiveAgent()._build_entry(signal)
    assert entry["id"] == signal["id"]
    assert entry["setup_id"] == build_setup_identity(signal)


def test_exact_signal_id_is_duplicate_even_if_open_timestamp_is_unavailable(monkeypatch):
    candidate = payload("SIGNAL_001")
    archive = [{
        "id": "SIGNAL_001",
        "symbol": "XAUUSD",
        "status": "OPEN",
        "setup_id": "different-setup",
    }]
    monkeypatch.setattr("agents.signal_reviewer.load_memory", lambda: {"_archive": archive})
    assert SignalReviewer("XAUUSD").is_duplicate(candidate)


def test_same_setup_is_duplicate_even_with_a_new_signal_id(monkeypatch):
    candidate = payload("SIGNAL_002")
    existing = payload("SIGNAL_001")
    archive = [{
        "id": existing["id"],
        "symbol": "XAUUSD",
        "status": "OPEN",
        "opened": opened(1),
        "setup_id": build_setup_identity(existing),
    }]
    monkeypatch.setattr("agents.signal_reviewer.load_memory", lambda: {"_archive": archive})
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
        "setup_id": build_setup_identity(existing),
        "entry": 4000.0,
    }]
    monkeypatch.setattr("agents.signal_reviewer.load_memory", lambda: {"_archive": archive})
    assert not SignalReviewer("XAUUSD").is_duplicate(candidate)


def test_old_setup_is_not_blocked_after_duplicate_window(monkeypatch):
    candidate = payload("SIGNAL_002")
    existing = payload("SIGNAL_001")
    archive = [{
        "id": existing["id"],
        "symbol": "XAUUSD",
        "status": "OPEN",
        "opened": opened(3),
        "setup_id": build_setup_identity(existing),
    }]
    monkeypatch.setattr("agents.signal_reviewer.load_memory", lambda: {"_archive": archive})
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
    monkeypatch.setattr("agents.signal_reviewer.load_memory", lambda: {"_archive": archive})
    assert SignalReviewer("XAUUSD").is_duplicate(candidate)


def test_setup_identity_rejects_non_finite_price_fields():
    candidate = payload()
    candidate["trigger"]["entry"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        build_setup_identity(candidate)
