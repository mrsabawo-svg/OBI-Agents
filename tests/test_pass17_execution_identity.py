import unittest
from datetime import datetime
from unittest.mock import patch

from agents.execution_agent import (
    save_pending,
    load_pending,
    clear_pending,
    ExecutionAgent,
)


class Pass17ExecutionIdentityTest(unittest.TestCase):
    def setUp(self):
        self.plan_a = {
            "signal_id": "BTCUSD_20260924_070000_SAST_aaaa1111",
            "symbol": "BTCUSD",
            "ticker": "BTCUSDT",
            "direction": "BUY",
            "qty": 1,
            "timestamp": "2026-09-25 07:00 SAST",
        }
        self.plan_b = {
            "signal_id": "BTCUSD_20260924_070001_SAST_bbbb2222",
            "symbol": "BTCUSD",
            "ticker": "BTCUSDT",
            "direction": "SELL",
            "qty": 2,
            "timestamp": "2026-09-25 07:01 SAST",
        }

    def test_multiple_pending_trades_are_stored_by_signal_id(self):
        memory = {}
        with patch("core.memory.load", return_value=memory),              patch("core.memory.save") as save:
            save_pending(self.plan_a)
            save_pending(self.plan_b)

        self.assertEqual(memory["_pending_trades"][self.plan_a["signal_id"]], self.plan_a)
        self.assertEqual(memory["_pending_trades"][self.plan_b["signal_id"]], self.plan_b)
        self.assertEqual(save.call_count, 2)

    def test_loading_one_signal_does_not_select_another(self):
        memory = {"_pending_trades": {
            self.plan_a["signal_id"]: self.plan_a,
            self.plan_b["signal_id"]: self.plan_b,
        }}
        with patch("core.memory.load", return_value=memory):
            self.assertEqual(load_pending(self.plan_a["signal_id"]), self.plan_a)
            self.assertEqual(load_pending(self.plan_b["signal_id"]), self.plan_b)
            self.assertEqual(load_pending("UNKNOWN"), {})

    def test_clearing_one_signal_preserves_other(self):
        memory = {"_pending_trades": {
            self.plan_a["signal_id"]: self.plan_a,
            self.plan_b["signal_id"]: self.plan_b,
        }}
        with patch("core.memory.load", return_value=memory),              patch("core.memory.save"):
            clear_pending(self.plan_a["signal_id"])

        self.assertNotIn(self.plan_a["signal_id"], memory["_pending_trades"])
        self.assertIn(self.plan_b["signal_id"], memory["_pending_trades"])

    def test_approval_uses_signal_id_not_symbol(self):
        memory = {"_pending_trades": {
            self.plan_a["signal_id"]: self.plan_a,
            self.plan_b["signal_id"]: self.plan_b,
        }}
        agent = ExecutionAgent("BTCUSD")
        with patch("agents.execution_agent.load_pending", return_value=self.plan_b):
            with patch("agents.execution_agent._executor") as executor:
                executor.place_order_safe.return_value = {
                    "status": "SUCCESS",
                    "data": {"orderId": "ORDER_17"},
                }
                with patch("agents.execution_agent.clear_pending"):
                    result = agent.approve(self.plan_b["signal_id"])
        self.assertIn("order placed", result)
        executor.place_order_safe.assert_called_once()

    def test_wrong_symbol_agent_cannot_execute_signal(self):
        agent = ExecutionAgent("ETHUSD")
        with patch("agents.execution_agent.load_pending", return_value=self.plan_b):
            result = agent.approve(self.plan_b["signal_id"])
        self.assertIn("Signal symbol mismatch", result)

    def test_save_requires_canonical_signal_id(self):
        memory = {}
        with patch("core.memory.load", return_value=memory),              patch("core.memory.save") as save:
            save_pending({"symbol": "BTCUSD"})
        self.assertNotIn("_pending_trades", memory)
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
