import json
from unittest.mock import Mock, patch

import pytest

import core.memory as memory
from agents.archive_agent import ArchiveAgent


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _gist_payload(data):
    return {
        "files": {
            "obi_memory.json": {
                "content": json.dumps(data),
            }
        }
    }


def test_load_returns_last_known_good_but_blocks_writes_after_remote_read_failure():
    known_good = {"_archive": [{"id": "durable-1"}]}
    with patch.object(memory, "_LAST_KNOWN_GOOD", None), patch.object(
        memory, "_READ_IS_STALE", True
    ):
        with patch("core.memory.requests.get", return_value=FakeResponse(
            200, _gist_payload(known_good)
        )):
            assert memory.load() == known_good

        with patch("core.memory.requests.get", return_value=FakeResponse(503, text="offline")):
            assert memory.load() == known_good

        patch_request = Mock()
        with patch("core.memory.requests.patch", patch_request):
            assert memory.save({"_archive": []}) is False
            patch_request.assert_not_called()


def test_load_fails_closed_when_no_trusted_snapshot_exists():
    with patch.object(memory, "_LAST_KNOWN_GOOD", None), patch.object(
        memory, "_READ_IS_STALE", True
    ), patch("core.memory.requests.get", return_value=FakeResponse(503, text="offline")):
        with pytest.raises(memory.MemoryReadError, match="empty fallback"):
            memory.load()


def test_save_reports_remote_success_and_failure():
    snapshot = {"_archive": [{"id": "durable-2"}]}
    with patch.object(memory, "_LAST_KNOWN_GOOD", None), patch.object(
        memory, "_READ_IS_STALE", False
    ):
        with patch("core.memory.requests.patch", return_value=FakeResponse(200)):
            assert memory.save(snapshot) is True
            assert memory._LAST_KNOWN_GOOD == snapshot
            assert memory._READ_IS_STALE is False

        with patch("core.memory.requests.patch", return_value=FakeResponse(500, text="error")):
            assert memory.save({"_archive": []}) is False
            assert memory._READ_IS_STALE is True


def test_archive_agent_does_not_report_success_when_gist_write_fails():
    with patch("agents.archive_agent.load_memory", return_value={}), patch(
        "agents.archive_agent.save_memory", return_value=False
    ):
        assert ArchiveAgent().log({"id": "not-durable", "symbol": "EURUSD"}) is False


def test_archive_agent_reports_success_only_after_confirmed_gist_write():
    with patch("agents.archive_agent.load_memory", return_value={}), patch(
        "agents.archive_agent.save_memory", return_value=True
    ):
        assert ArchiveAgent().log({"id": "durable", "symbol": "EURUSD"}) is True
