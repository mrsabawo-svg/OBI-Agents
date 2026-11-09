import json
from unittest.mock import Mock, patch

import pytest

import core.memory as memory
from agents.archive_agent import ArchiveAgent


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.payload = payload or {}

    def json(self):
        return self.payload


def gist(data):
    return {"files": {"obi_memory.json": {"content": json.dumps(data)}}}


@pytest.fixture(autouse=True)
def reset_memory(monkeypatch):
    monkeypatch.setattr(memory, "GIST_ID", "test-gist")
    monkeypatch.setattr(memory, "GH_TOKEN", "test-token")
    monkeypatch.setattr(memory, "HEADERS", {})
    monkeypatch.setattr(memory, "_LAST_KNOWN_GOOD", None)
    monkeypatch.setattr(memory, "_READ_IS_STALE", True)
    monkeypatch.setattr(memory, "_BASE_REVISION", None)


def test_load_falls_back_read_only_and_blocks_save():
    good = {"_archive": [{"id": "known"}]}
    with patch("core.memory.requests.get", side_effect=[FakeResponse(200, gist(good)), FakeResponse(503)]):
        assert memory.load() == good
        assert memory.load() == good
    patch_call = Mock()
    with patch("core.memory.requests.patch", patch_call):
        assert memory.save({"_archive": []}) is False
        patch_call.assert_not_called()


def test_load_fails_closed_without_last_known_good():
    with patch("core.memory.requests.get", return_value=FakeResponse(503)):
        with pytest.raises(memory.MemoryReadError, match="empty fallback"):
            memory.load()


@pytest.mark.parametrize("info", [
    None,
    {"content": ""},
    {"content": "[]"},
    {"content": "{"},
    {"content": "{}", "truncated": True},
])
def test_load_rejects_invalid_canonical_memory(info):
    files = {} if info is None else {"obi_memory.json": info}
    with patch("core.memory.requests.get", return_value=FakeResponse(200, {"files": files})):
        with pytest.raises(memory.MemoryReadError):
            memory.load()


def test_save_requires_fresh_revision_and_detects_conflict():
    initial = {"items": ["a"]}
    changed = {"items": ["a", "remote"], "_memory_meta": {"schema_version": 1, "revision": 1, "transactions": []}}
    with patch("core.memory.requests.get", side_effect=[FakeResponse(200, gist(initial)), FakeResponse(200, gist(changed))]), \
         patch("core.memory.requests.patch") as patch_call:
        assert memory.load() == initial
        assert memory.save({"items": ["mine"]}) is False
        patch_call.assert_not_called()


def test_save_readback_verifies_revision_and_payload():
    initial = {"items": ["a"]}
    expected = {"items": ["a", "b"], "_memory_meta": {"schema_version": 1, "revision": 1, "transactions": []}}
    with patch("core.memory.requests.get", side_effect=[
        FakeResponse(200, gist(initial)), FakeResponse(200, gist(initial)), FakeResponse(200, gist(expected))
    ]), patch("core.memory.requests.patch", return_value=FakeResponse(200)) as patch_call:
        assert memory.load() == initial
        assert memory.save({"items": ["a", "b"]}) is True
        sent = patch_call.call_args.kwargs["json"]["files"]["obi_memory.json"]["content"]
        assert json.loads(sent) == expected
        assert memory._BASE_REVISION == 1


def test_save_fails_when_readback_differs():
    initial = {"items": ["a"]}
    wrong = {"items": ["wrong"], "_memory_meta": {"schema_version": 1, "revision": 1, "transactions": []}}
    with patch("core.memory.requests.get", side_effect=[
        FakeResponse(200, gist(initial)), FakeResponse(200, gist(initial)), FakeResponse(200, gist(wrong))
    ]), patch("core.memory.requests.patch", return_value=FakeResponse(200)):
        assert memory.load() == initial
        assert memory.save({"items": ["a", "b"]}) is False


def test_archive_agent_only_reports_confirmed_persistence():
    with patch("agents.archive_agent.load_memory", return_value={}), patch("agents.archive_agent.save_memory", return_value=True):
        assert ArchiveAgent().log({"id": "durable", "symbol": "EURUSD"}) is True
    with patch("agents.archive_agent.load_memory", return_value={}), patch("agents.archive_agent.save_memory", return_value=False):
        assert ArchiveAgent().log({"id": "not-durable", "symbol": "EURUSD"}) is False
