import unittest
from unittest.mock import patch, MagicMock
from agents.lifecycle_agent import LifecycleAgent, SAST
from agents.execution_agent import ExecutionAgent, execution_order_client_id
from agents.archive_agent import ArchiveAgent
from agents.factor_agent import FactorAgent


class AuditFindingsRegressionTests(unittest.TestCase):

    def _trade(self, direction="SELL"):
        return {
            "id": "TEST_1",
            "symbol": "BTCUSD",
            "status": "OPEN",
            "direction": direction,
            "entry": 100.0,
            "sl": 110.0 if direction == "SELL" else 90.0,
            "tp1": 99.0 if direction == "SELL" else 101.0,
            "tp2": 98.0 if direction == "SELL" else 102.0,
            "tp3": 97.0 if direction == "SELL" else 103.0,
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
        }

    def test_sell_tp1_persists_as_changed_event(self):
        trade = self._trade("SELL")
        changed = LifecycleAgent()._apply_candle(trade, high=100.5, low=99.0, now=SAST.localize(__import__("datetime").datetime(2026, 9, 25, 8, 0)))
        self.assertTrue(changed)
        self.assertTrue(trade["tp1_hit"])

    def test_ambiguous_candle_is_persistable_and_marked(self):
        trade = self._trade("SELL")
        changed = LifecycleAgent()._apply_candle(trade, high=110.0, low=99.0, now=SAST.localize(__import__("datetime").datetime(2026, 9, 25, 8, 0)))
        self.assertTrue(changed)
        self.assertEqual(trade["resolution_note"], "AMBIGUOUS_SAME_CANDLE_TARGET_AND_STOP")

    @patch("agents.execution_agent.clear_pending")
    @patch("agents.execution_agent._save_execution_state")
    @patch("agents.execution_agent.load_execution_state")
    @patch("agents.execution_agent.load_pending")
    def test_submitting_state_recovers_from_exchange(self, load_pending, load_state, save_state, clear_pending):
        signal_id = "BTCUSD_20260925_070000_SAST_a1b2c3d4"
        plan = {
            "signal_id": signal_id, "symbol": "BTCUSD", "ticker": "BTCUSDT",
            "direction": "BUY", "entry": 100000.0, "sl": 99000.0,
            "tp1": 102000.0, "tp2": 104000.0, "tp3": 106000.0,
            "qty": 1.0, "timestamp": "2099-01-01 07:00 SAST",
        }
        load_pending.return_value = plan
        load_state.return_value = {
            "signal_id": signal_id, "symbol": "BTCUSD", "status": "SUBMITTING",
            "order_client_id": execution_order_client_id(signal_id),
        }
        with patch("agents.execution_agent._executor.find_order_by_client_id",
                   return_value={"orderId": "RECOVERED"}):
            result = ExecutionAgent("BTCUSD").approve(signal_id)
        self.assertIn("Recovered existing exchange order", result)
        self.assertEqual(save_state.call_args.args[0]["status"], "EXECUTED")
        clear_pending.assert_called_once_with(signal_id)

    def test_archive_exposes_factor_schema(self):
        signal = {
            "id": "BTCUSD_1",
            "symbol": "BTCUSD",
            "trigger": {"direction": "BUY", "grade": "B", "entry": 100, "sl": 90,
                        "tp1": 101, "tp2": 102, "tp3": 103, "rr": 3,
                        "tags": ["OB"], "confluence": 2},
            "regime": {"label": "TRENDING", "confidence": 0.8},
            "bias": {"score": 70, "factors": ["HTF", "VWAP"]},
            "htf": {}, "mtf": {}, "score": {"grade": "A", "confidence": 82},
        }
        entry = ArchiveAgent()._build_entry(signal)
        self.assertEqual(entry["factors"], ["HTF", "VWAP"])
        self.assertEqual(entry["obi_score"], 82)

    def test_factor_reader_accepts_canonical_archive_fields(self):
        factor = FactorAgent()
        trades = [
            {"factors": ["HTF"], "bias_factors": ["HTF"], "obi_score": 80,
             "status": "CLOSED", "outcome": "TP3", "symbol": "BTCUSD",
             "regime": "TRENDING", "grade": "A"}
        ] * 5
        self.assertEqual(factor._by_factor(trades)["HTF"]["n"], 5)
        self.assertIn("70+", factor._by_score_bucket(trades))


if __name__ == "__main__":
    unittest.main()
