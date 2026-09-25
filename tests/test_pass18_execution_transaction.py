import unittest
from unittest.mock import MagicMock, patch

from agents.execution_agent import (
    ExecutionAgent,
    execution_order_client_id,
    load_execution_state,
    save_pending,
)


class Pass18ExecutionTransactionTest(unittest.TestCase):
    def setUp(self):
        self.plan = {
            "signal_id": "BTCUSD_20260925_070000_SAST_a1b2c3d4",
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
            "timestamp": "2099-01-01 07:00 SAST",
        }

    def test_order_client_id_is_deterministic_and_bybit_safe(self):
        first = execution_order_client_id(self.plan["signal_id"])
        second = execution_order_client_id(self.plan["signal_id"])
        self.assertEqual(first, second)
        self.assertLessEqual(len(first), 36)
        self.assertTrue(first.startswith("obi-"))
        self.assertNotEqual(first, execution_order_client_id("OTHER_SIGNAL"))

    def test_approval_binds_order_to_canonical_signal_and_persists_executed_state(self):
        memory = {
            "_pending_trades": {self.plan["signal_id"]: self.plan},
            "_execution_state": {},
        }
        with patch("core.memory.load", return_value=memory),              patch("core.memory.save"),              patch("agents.execution_agent._executor") as executor,              patch("agents.execution_agent._send"):
            executor.place_order_safe.return_value = {
                "status": "SUCCESS",
                "data": {"orderId": "ORDER_18", "orderLinkId": execution_order_client_id(self.plan["signal_id"])},
            }
            result = ExecutionAgent("BTCUSD").approve(self.plan["signal_id"])

        self.assertIn("order placed", result)
        self.assertEqual(memory["_execution_state"][self.plan["signal_id"]]["status"], "EXECUTED")
        self.assertEqual(
            memory["_execution_state"][self.plan["signal_id"]]["order_client_id"],
            execution_order_client_id(self.plan["signal_id"]),
        )
        self.assertEqual(memory["_execution_state"][self.plan["signal_id"]]["order_id"], "ORDER_18")
        params = executor.place_order_safe.call_args.args[0]
        self.assertEqual(params["orderClientId"], execution_order_client_id(self.plan["signal_id"]))

    def test_repeated_approval_after_success_never_submits_again(self):
        memory = {
            "_pending_trades": {},
            "_execution_state": {
                self.plan["signal_id"]: {
                    "signal_id": self.plan["signal_id"],
                    "symbol": "BTCUSD",
                    "status": "EXECUTED",
                    "order_client_id": execution_order_client_id(self.plan["signal_id"]),
                    "order_id": "ORDER_18",
                }
            },
        }
        with patch("core.memory.load", return_value=memory),              patch("agents.execution_agent._executor") as executor:
            result = ExecutionAgent("BTCUSD").approve(self.plan["signal_id"])

        self.assertIn("already executed", result)
        executor.place_order_safe.assert_not_called()

    def test_recoverable_failure_preserves_pending_and_state(self):
        memory = {
            "_pending_trades": {self.plan["signal_id"]: self.plan},
            "_execution_state": {},
        }
        with patch("core.memory.load", return_value=memory),              patch("core.memory.save"),              patch("agents.execution_agent._executor") as executor,              patch("agents.execution_agent._send"):
            executor.place_order_safe.return_value = {
                "status": "FAILED",
                "reason": "MAX_RETRIES_EXCEEDED",
            }
            result = ExecutionAgent("BTCUSD").approve(self.plan["signal_id"])

        self.assertIn("recoverable", result.lower())
        self.assertIn(self.plan["signal_id"], memory["_pending_trades"])
        self.assertEqual(
            memory["_execution_state"][self.plan["signal_id"]]["status"],
            "FAILED_RECOVERABLE",
        )

    def test_fatal_failure_is_persisted_and_not_cleared(self):
        memory = {
            "_pending_trades": {self.plan["signal_id"]: self.plan},
            "_execution_state": {},
        }
        with patch("core.memory.load", return_value=memory),              patch("core.memory.save"),              patch("agents.execution_agent._executor") as executor,              patch("agents.execution_agent._send"):
            executor.place_order_safe.return_value = {
                "status": "FAILED",
                "reason": "FATAL_API_ERROR",
                "code": 110004,
            }
            result = ExecutionAgent("BTCUSD").approve(self.plan["signal_id"])

        self.assertIn("failed", result.lower())
        self.assertIn(self.plan["signal_id"], memory["_pending_trades"])
        self.assertEqual(
            memory["_execution_state"][self.plan["signal_id"]]["status"],
            "FAILED_FATAL",
        )

    def test_wrong_symbol_never_reaches_executor(self):
        memory = {
            "_pending_trades": {self.plan["signal_id"]: self.plan},
            "_execution_state": {},
        }
        with patch("core.memory.load", return_value=memory),              patch("agents.execution_agent._executor") as executor:
            result = ExecutionAgent("ETHUSD").approve(self.plan["signal_id"])

        self.assertIn("symbol mismatch", result.lower())
        executor.place_order_safe.assert_not_called()

    def test_telegram_resolves_symbol_from_pending_signal_not_string_parsing(self):
        import agents.telegram_command_agent as commands
        with patch.object(commands, "OPERATOR_ID", "42"),              patch("agents.execution_agent.load_pending", return_value=self.plan),              patch("agents.execution_agent.ExecutionAgent") as agent_cls:
            agent_cls.return_value.approve.return_value = "approved"
            result = commands.handle_approve(
                self.plan["signal_id"],
                "42",
            )

        self.assertEqual(result, "approved")
        agent_cls.assert_called_once_with("BTCUSD")
        agent_cls.return_value.approve.assert_called_once_with(self.plan["signal_id"])

    def test_save_pending_requires_signal_id(self):
        memory = {"_pending_trades": {}}
        with patch("core.memory.load", return_value=memory),              patch("core.memory.save") as save:
            save_pending({"symbol": "BTCUSD"})
        self.assertEqual(memory["_pending_trades"], {})
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
