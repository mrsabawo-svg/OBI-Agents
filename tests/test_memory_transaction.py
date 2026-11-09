import json
from unittest.mock import patch

import pytest

import core.memory as memory


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.payload = payload or {}

    def json(self):
        return self.payload


def gist(data):
    return {"files": {"obi_memory.json": {"content": json.dumps(data)}}}


def configure(monkeypatch):
    monkeypatch.setattr(memory, "GIST_ID", "test-gist")
    monkeypatch.setattr(memory, "GH_TOKEN", "test-token")
    monkeypatch.setattr(memory, "HEADERS", {})
    monkeypatch.setattr(memory, "_LAST_KNOWN_GOOD", None)
    monkeypatch.setattr(memory, "_READ_IS_STALE", True)
    monkeypatch.setattr(memory, "_BASE_REVISION", None)


def test_transaction_updates_fresh_snapshot_and_records_key(monkeypatch):
    configure(monkeypatch)
    initial = {"counter": 1}
    final = {"counter": 2, "_memory_meta": {
        "schema_version": 1, "revision": 1,
        "transactions": [{"id": "event-1", "digest": memory._digest({"counter": 2})}],
    }}
    with patch("core.memory.requests.get", side_effect=[
        FakeResponse(200, gist(initial)), FakeResponse(200, gist(initial)), FakeResponse(200, gist(final))
    ]), patch("core.memory.requests.patch", return_value=FakeResponse(200)):
        assert memory.transaction(lambda state: state.update(counter=2), "event-1") is True
        assert memory._LAST_KNOWN_GOOD == final


def test_same_transaction_id_and_payload_is_idempotent(monkeypatch):
    configure(monkeypatch)
    current = {"counter": 2, "_memory_meta": {
        "schema_version": 1, "revision": 1,
        "transactions": [{"id": "event-1", "digest": memory._digest({"counter": 2})}],
    }}
    with patch("core.memory.requests.get", return_value=FakeResponse(200, gist(current))), \
         patch("core.memory.requests.patch") as patch_call:
        assert memory.load() == current
        assert memory.save({"counter": 2}, transaction_id="event-1") is True
        patch_call.assert_not_called()


def test_reused_transaction_id_with_different_payload_is_rejected(monkeypatch):
    configure(monkeypatch)
    current = {"counter": 2, "_memory_meta": {
        "schema_version": 1, "revision": 1,
        "transactions": [{"id": "event-1", "digest": memory._digest({"counter": 2})}],
    }}
    with patch("core.memory.requests.get", return_value=FakeResponse(200, gist(current))), \
         patch("core.memory.requests.patch") as patch_call:
        assert memory.load() == current
        assert memory.save({"counter": 3}, transaction_id="event-1") is False
        patch_call.assert_not_called()


def test_mutator_failure_does_not_write(monkeypatch):
    configure(monkeypatch)
    with patch("core.memory.requests.get", return_value=FakeResponse(200, gist({"counter": 1}))), \
         patch("core.memory.requests.patch") as patch_call:
        with pytest.raises(ValueError):
            memory.transaction(lambda _state: (_ for _ in ()).throw(ValueError("invalid")), "event-bad")
        patch_call.assert_not_called()


def test_invalid_mutator_result_does_not_write(monkeypatch):
    configure(monkeypatch)
    with patch("core.memory.requests.get", return_value=FakeResponse(200, gist({"counter": 1}))), \
         patch("core.memory.requests.patch") as patch_call:
        with pytest.raises(TypeError):
            memory.transaction(lambda _state: [], "event-bad")
        patch_call.assert_not_called()
