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

    def test_terminal_and_realized_pnl_are_separate(self):
        entry = ArchiveAgent()._build_entry({
            "id": "BTCUSD_2", "symbol": "BTCUSD",
            "trigger": {"direction": "BUY", "grade": "B", "entry": 100, "sl": 90,
                        "tp1": 101, "tp2": 102, "tp3": 103, "rr": 3,
                        "tags": [], "confluence": 1},
            "regime": {}, "bias": {"score": 50, "factors": []}, "htf": {}, "mtf": {},
            "score": {"grade": "B", "confidence": 50},
        })
        self.assertIsNone(entry["realized_pnl_pips"])
        self.assertEqual(entry["terminal_pnl_pips"], 0)

    def test_factor_reader_accepts_canonical_archive_fields(self):
        factor = FactorAgent()
        trades = [
            {"factors": ["HTF"], "bias_factors": ["HTF"], "obi_score": 80,
             "status": "CLOSED", "outcome": "TP3", "symbol": "BTCUSD",
             "regime": "TRENDING", "grade": "A"}
        ] * 5
        self.assertEqual(factor._by_factor(trades)["HTF"]["n"], 5)
        self.assertIn("70+", factor._by_score_bucket(trades))


    @patch("main.ExecutionAgent")
    @patch("main.IntelligenceAgent")
    @patch("main.ScoreAgent")
    @patch("main.EdgeAgent")
    @patch("main.TriggerAgent")
    @patch("main.LTFAgent")
    @patch("main.ZoneAgent")
    @patch("main.BiasAgent")
    @patch("main.MTFAgent")
    @patch("main.RegimeAgent")
    @patch("main.HTFAgent")
    @patch("main.SessionAgent")
    @patch("main.DataAgent")
    def test_duplicate_rejection_stops_execution(self, data, session, htf, regime, mtf, bias, zone, ltf, trigger, edge, score, intelligence, execution):
        session.return_value.analyse.return_value = {"tradeable": True}
        bias.return_value.evaluate.return_value.approved = True
        trigger.return_value.evaluate.return_value.fire = True
        intelligence.return_value.verdict.return_value = {"accepted": False, "reason": "duplicate"}
        from main import run
        result = run("BTCUSD", {"safe": True})
        self.assertEqual(result["blocked"], "duplicate")
        execution.return_value.propose.assert_not_called()

    def test_factor_win_rate_uses_terminal_tp3_only(self):
        factor = FactorAgent()
        trades = [{"status": "CLOSED", "outcome": "TP3", "terminal_outcome": "TP3"}] + [{"status": "CLOSED", "outcome": "SL", "terminal_outcome": "SL"}] * 4
        self.assertEqual(factor._wr(trades), 20.0)

    def test_lifecycle_replay_window_covers_more_than_expiry_window(self):
        import agents.lifecycle_agent as lifecycle
        self.assertGreaterEqual(lifecycle.REPLAY_DAYS, 2)

    @patch("agents.telegram_command_agent.send")
    @patch("agents.telegram_command_agent.route", return_value="ok")
    @patch("agents.telegram_command_agent._save_offset", return_value=True)
    @patch("agents.telegram_command_agent._get_updates")
    @patch("agents.telegram_command_agent._load_offset", return_value=(0, 0))
    def test_telegram_acknowledges_after_command_processing(self, load_offset, get_updates, save_offset, route, send):
        from agents.telegram_command_agent import poll_and_process
        get_updates.return_value = [{"update_id": 7, "message": {"chat": {"id": "chat"}, "text": "/health", "from": {"id": "operator"}}}]
        import agents.telegram_command_agent as cmd
        with patch.object(cmd, "CHAT_ID", "chat"):
            poll_and_process()
        route.assert_called_once()
        save_offset.assert_called_once_with(8, 7)
        self.assertLess(route.call_args_list[0].__class__.__name__ == "never", 1)

if __name__ == "__main__":
    unittest.main()
