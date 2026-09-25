import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from agents.execution_agent import (
    save_pending,
    load_pending,
    clear_pending,
    ExecutionAgent,
    SAST,
)


class Pass17ExecutionIdentityTest(unittest.TestCase):
    def setUp(self):
        now = datetime.now(SAST).replace(second=0, microsecond=0)
        self.plan_time = now - timedelta(minutes=1)
        timestamp = self.plan_time.strftime("%Y-%m-%d %H:%M SAST")

        self.plan_a = {
            "signal_id": "BTCUSD_SIGNAL_A",
            "symbol": "BTCUSD",
            "ticker": "BTCUSDT",
            "direction": "BUY",
            "entry": 100000.0,
            "sl": 99000.0,
            "tp1": 102000.0,
            "tp2": 104000.0,
            "tp3": 106000.0,
            "rr": 2.0,
            "confidence": 80,
            "grade": "A",
            "risk": "LOW",
            "risk_pct": 1.0,
            "balance": 1000.0,
            "qty": 1.0,
            "timestamp": timestamp,
        }
        self.plan_b = dict(self.plan_a)
        self.plan_b.update({
            "signal_id": "BTCUSD_SIGNAL_B",
            "direction": "SELL",
            "sl": 101000.0,
            "tp1": 98000.0,
            "tp2": 96000.0,
            "tp3": 94000.0,
            "qty": 2.0,
        })

    def test_multiple_pending_trades_are_stored_by_signal_id(self):
        memory = {"_pending_trades": {}}
        with patch("core.memory.load", return_value=memory),              patch("core.memory.save") as save:
            save_pending(self.plan_a)
            save_pending(self.plan_b)

        self.assertEqual(memory["_pending_trades"][self.plan_a["signal_id"]], self.plan_a)
        self.assertEqual(memory["_pending_trades"][self.plan_b["signal_id"]], self.plan_b)
        self.assertEqual(len(memory["_pending_trades"]), 2)
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
        agent = ExecutionAgent("BTCUSD")

        with patch(
            "agents.execution_agent.load_pending",
            side_effect=lambda signal_id: {
                self.plan_a["signal_id"]: self.plan_a,
                self.plan_b["signal_id"]: self.plan_b,
            }.get(signal_id, {}),
        ),              patch("agents.execution_agent._executor") as executor,              patch("agents.execution_agent.clear_pending"),              patch("agents.execution_agent._send"):
            executor.place_order_safe.return_value = {
                "status": "SUCCESS",
                "data": {"orderId": "ORDER_17"},
            }

            result = agent.approve(self.plan_b["signal_id"])

        self.assertIn("Order placed", result)
        executor.place_order_safe.assert_called_once()
        order_params = executor.place_order_safe.call_args.args[0]
        self.assertEqual(order_params["symbol"], "BTCUSDT")
        self.assertEqual(order_params["side"], "Sell")
        self.assertEqual(order_params["qty"], "2.0")
        self.assertEqual(order_params["stopLoss"], "101000.0")
        self.assertEqual(order_params["takeProfit"], "94000.0")

    def test_wrong_symbol_agent_cannot_execute_signal(self):
        agent = ExecutionAgent("ETHUSD")
        with patch(
            "agents.execution_agent.load_pending",
            return_value=self.plan_b,
        ),              patch("agents.execution_agent._executor") as executor:
            result = agent.approve(self.plan_b["signal_id"])

        self.assertIn("Signal symbol mismatch", result)
        executor.place_order_safe.assert_not_called()

    def test_save_requires_canonical_signal_id(self):
        memory = {"_pending_trades": {}}
        with patch("core.memory.load", return_value=memory),              patch("core.memory.save") as save:
            save_pending({"symbol": "BTCUSD"})

        self.assertEqual(memory["_pending_trades"], {})
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
