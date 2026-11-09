from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace
from threading import Barrier

import pytest

from agents.signal_reviewer import SignalReviewer, SAST
from core.signal_identity import SETUP_IDENTITY_VERSION, build_setup_identity


def candidate(signal_id="event-new"):
    return {
        "id": signal_id,
        "symbol": "XAUUSD",
        "trigger": {
            "direction": "BUY",
            "entry": 4000,
            "sl": 3990,
            "tp1": 4010,
            "tp2": 4020,
            "tp3": 4030,
            "tags": ["OB", "FVG"],
        },
        "regime": {"label": "TRENDING"},
    }


def opened(hours_ago=1):
    return (datetime.now(SAST) - timedelta(hours=hours_ago)).strftime(
        "%Y-%m-%d %H:%M SAST"
    )


def archive_record(payload=None, **overrides):
    payload = payload or candidate("event-old")
    record = {
        "id": payload["id"],
        "symbol": payload["symbol"],
        "status": "OPEN",
        "opened": opened(),
        "direction": payload["trigger"]["direction"],
        "entry": payload["trigger"]["entry"],
        "sl": payload["trigger"]["sl"],
        "tp1": payload["trigger"]["tp1"],
        "tp2": payload["trigger"]["tp2"],
        "tp3": payload["trigger"]["tp3"],
        "tags": payload["trigger"]["tags"],
        "regime": payload["regime"]["label"],
        "setup_identity_version": SETUP_IDENTITY_VERSION,
        "setup_id": build_setup_identity(payload),
    }
    record.update(overrides)
    return record


@pytest.fixture
def fresh_memory(monkeypatch):
    snapshot = {"_archive": []}
    monkeypatch.setattr("agents.signal_reviewer.memory_store.load_with_status", lambda: (snapshot, False))
    return snapshot


def test_corrupt_archive_container_fails_closed(fresh_memory):
    fresh_memory["_archive"] = {"not": "a list"}
    reviewer = SignalReviewer("XAUUSD")
    assert reviewer.is_duplicate(candidate()) is True
    assert reviewer.duplicate_check_status == "unavailable"


def test_malformed_archive_record_fails_closed(fresh_memory):
    fresh_memory["_archive"] = [None]
    reviewer = SignalReviewer("XAUUSD")
    assert reviewer.is_duplicate(candidate()) is True
    assert reviewer.duplicate_check_status == "unavailable"


def test_memory_read_failure_fails_closed(monkeypatch):
    def fail_read():
        raise RuntimeError("remote memory unavailable")

    monkeypatch.setattr("agents.signal_reviewer.memory_store.load_with_status", fail_read)
    reviewer = SignalReviewer("XAUUSD")
    assert reviewer.is_duplicate(candidate()) is True
    assert reviewer.duplicate_check_status == "unavailable"


def test_stale_last_known_good_snapshot_fails_closed(fresh_memory, monkeypatch):
    monkeypatch.setattr("agents.signal_reviewer.memory_store.load_with_status", lambda: (fresh_memory, True))
    reviewer = SignalReviewer("XAUUSD")
    assert reviewer.is_duplicate(candidate()) is True
    assert reviewer.duplicate_check_status == "unavailable"


def test_malformed_candidate_identity_fails_closed(fresh_memory):
    signal = candidate()
    signal["signal_identity"] = {
        "signal_id": signal["id"],
        "setup_id": "not-a-sha256",
        "setup_identity_version": SETUP_IDENTITY_VERSION,
    }
    reviewer = SignalReviewer("XAUUSD")
    assert reviewer.is_duplicate(signal) is True
    assert reviewer.duplicate_check_status == "unavailable"


def test_conflicting_archive_identity_version_fails_closed(fresh_memory):
    fresh_memory["_archive"] = [archive_record(setup_identity_version="obi-setup-identity-v2")]
    reviewer = SignalReviewer("XAUUSD")
    assert reviewer.is_duplicate(candidate()) is True
    assert reviewer.duplicate_check_status == "unavailable"


def test_versioned_archive_digest_conflict_fails_closed(fresh_memory):
    fresh_memory["_archive"] = [archive_record(setup_id="a" * 64)]
    reviewer = SignalReviewer("XAUUSD")
    assert reviewer.is_duplicate(candidate()) is True
    assert reviewer.duplicate_check_status == "unavailable"


def test_legacy_record_is_reconstructed_only_when_identity_fields_are_absent(fresh_memory):
    record = archive_record()
    record.pop("setup_identity_version")
    record.pop("setup_id")
    fresh_memory["_archive"] = [record]
    reviewer = SignalReviewer("XAUUSD")
    assert reviewer.is_duplicate(candidate()) is True
    assert reviewer.duplicate_check_status == "duplicate"


def test_partial_legacy_identity_fails_closed(fresh_memory):
    record = archive_record()
    record.pop("setup_identity_version")
    fresh_memory["_archive"] = [record]
    reviewer = SignalReviewer("XAUUSD")
    assert reviewer.is_duplicate(candidate()) is True
    assert reviewer.duplicate_check_status == "unavailable"


def test_concurrent_same_symbol_submissions_are_serialized_through_archive_commit(monkeypatch):
    from agents.intelligence_agent import IntelligenceAgent
    from agents.signal_reviewer import SignalReviewer
    from agents.archive_agent import ArchiveAgent
    from agents.persistence_agent import PersistenceAgent
    from agents.notifier_agent import NotifierAgent

    archive = []
    barrier = Barrier(2)
    trigger = SimpleNamespace(
        direction="BUY", grade="A", entry=4000, sl=3990,
        tp1=4010, tp2=4020, tp3=4030, rr=2.0, tags=["OB"], fire=True
    )
    payload = {
        "id": "concurrent-event",
        "symbol": "XAUUSD",
        "trigger": trigger,
        "regime": {"label": "TRENDING"},
        "score": {},
        "edge": {},
    }

    def duplicate_check(self, signal):
        return any(item["id"] == signal["id"] for item in archive)

    def archive_log(self, signal):
        archive.append({"id": signal["id"]})
        return True

    monkeypatch.setattr(SignalReviewer, "is_duplicate", duplicate_check)
    monkeypatch.setattr(SignalReviewer, "review", lambda self, p, a: {
        "groq_verdict": "WAIT", "skeptic_verdict": "PARTIAL"
    })
    monkeypatch.setattr(SignalReviewer, "build_narrative", lambda self, p: "narrative")
    monkeypatch.setattr("agents.intelligence_agent.load_memory", lambda: {})
    monkeypatch.setattr(ArchiveAgent, "log", archive_log)
    monkeypatch.setattr(PersistenceAgent, "save", lambda self, result, signal: None)
    monkeypatch.setattr(NotifierAgent, "send", lambda self, result, narrative: None)

    def submit():
        barrier.wait()
        return IntelligenceAgent("XAUUSD").verdict(payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))

    assert len(archive) == 1
    assert sum(bool(result) for result in results) == 1
