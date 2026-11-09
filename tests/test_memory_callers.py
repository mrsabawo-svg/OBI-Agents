import importlib

import core.memory as memory
from agents import execution_agent, health_agent


def test_execution_pending_helpers_return_false_when_memory_write_fails(monkeypatch):
    monkeypatch.setattr(memory, "load", lambda: {"_pending_trade": {"symbol": "BTCUSD"}})
    monkeypatch.setattr(memory, "save", lambda _data: False)
    assert execution_agent.save_pending({"symbol": "BTCUSD"}) is False
    assert execution_agent.clear_pending() is False


def test_health_agent_does_not_claim_last_healthy_was_persisted(monkeypatch):
    monkeypatch.setattr(health_agent, "load_memory", lambda: {})
    monkeypatch.setattr(health_agent, "save_memory", lambda _data: False)
    assert health_agent.HealthAgent()._update_last_healthy() is False


def test_telegram_offset_save_checks_persistence_result(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    import agents.telegram_command_agent as commands
    commands = importlib.reload(commands)
    monkeypatch.setattr(memory, "load", lambda: {})
    monkeypatch.setattr(memory, "save", lambda _data: False)
    assert commands._save_offset(10, 9, retries=1) is False
