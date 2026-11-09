from unittest.mock import patch

from agents.persistence_agent import PersistenceAgent


class FakeScore:
    confidence = 75


def test_persistence_does_not_create_event_identity_when_missing():
    memory = {"_archive": [{"id": "existing-archive-record"}]}
    payload = {"symbol": "EURUSD", "score": FakeScore(), "regime": {"label": "TRENDING"}}
    result = {
        "timestamp": "2026-11-09 10:00 SAST",
        "direction": "BUY",
        "grade": "B",
        "entry": 1.1,
        "sl": 1.09,
        "tp1": 1.11,
        "tp2": 1.12,
        "tp3": 1.13,
        "rr": 2.0,
        "tags": [],
    }

    with patch("agents.persistence_agent.save_memory", return_value=True):
        PersistenceAgent("EURUSD")._update_memory(memory, result, payload)

    assert memory["EURUSD"]["last_signal_data"]["id"] is None
    assert memory["_archive"] == [{"id": "existing-archive-record"}]
