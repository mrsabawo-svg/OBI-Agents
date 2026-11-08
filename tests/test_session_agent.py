from datetime import datetime
from unittest.mock import patch

from agents.session_agent import SessionAgent


def test_session_agent_uses_one_captured_sast_clock_for_decision_and_trace():
    captured = datetime.fromisoformat("2026-11-09T09:30:00+02:00")
    with patch("agents.session_agent.now_sast", return_value=captured), patch(
        "agents.session_agent.is_kill_zone",
        return_value={"active": True, "name": "London Open"},
    ) as kill_zone:
        result = SessionAgent("EURUSD").analyse()

    kill_zone.assert_called_once_with(captured)
    assert result["tradeable"] is True
    assert result["evaluated_at_sast"] == captured.isoformat()


def test_session_agent_fails_closed_if_clock_cannot_be_read():
    with patch(
        "agents.session_agent.now_sast",
        side_effect=RuntimeError("clock unavailable"),
    ):
        result = SessionAgent("EURUSD").analyse()

    assert result["tradeable"] is False
    assert result["reason"] == "Session check failed"
    assert result["evaluated_at_sast"] is None
