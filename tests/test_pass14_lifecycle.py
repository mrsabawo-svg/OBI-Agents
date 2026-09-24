import unittest
from datetime import datetime
from agents.lifecycle_agent import LifecycleAgent, SAST


class Pass14OutcomeStateMachineTest(unittest.TestCase):
    def setUp(self):
        self.lifecycle = LifecycleAgent()
        self.now = SAST.localize(datetime.strptime(
            "2026-09-24 07:00", "%Y-%m-%d %H:%M"
        ))

    def trade(self, direction="BUY"):
        return {
            "id": "PASS14_001",
            "symbol": "XAUUSD",
            "direction": direction,
            "entry": 4000.0,
            "sl": 3990.0 if direction == "BUY" else 4010.0,
            "tp1": 4010.0 if direction == "BUY" else 3990.0,
            "tp2": 4020.0 if direction == "BUY" else 3980.0,
            "tp3": 4030.0 if direction == "BUY" else 3970.0,
            "status": "OPEN",
            "outcome": "PENDING",
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
        }

    def test_buy_progresses_tp1_tp2_then_closes_at_tp3(self):
        trade = self.trade("BUY")

        self.assertTrue(self.lifecycle._apply_candle(trade, 4011, 4001, self.now))
        self.assertTrue(trade["tp1_hit"])
        self.assertFalse(trade["tp2_hit"])
        self.assertEqual(trade["status"], "OPEN")
        self.assertEqual(trade["outcome"], "PENDING")

        self.assertTrue(self.lifecycle._apply_candle(trade, 4021, 4011, self.now))
        self.assertTrue(trade["tp2_hit"])
        self.assertFalse(trade["tp3_hit"])
        self.assertEqual(trade["status"], "OPEN")

        self.assertTrue(self.lifecycle._apply_candle(trade, 4031, 4021, self.now))
        self.assertTrue(trade["tp3_hit"])
        self.assertEqual(trade["status"], "CLOSED")
        self.assertEqual(trade["outcome"], "TP3")

    def test_sell_progresses_tp1_tp2_then_closes_at_tp3(self):
        trade = self.trade("SELL")

        self.assertTrue(self.lifecycle._apply_candle(trade, 3999, 3989, self.now))
        self.assertTrue(trade["tp1_hit"])
        self.assertEqual(trade["status"], "OPEN")

        self.assertTrue(self.lifecycle._apply_candle(trade, 3989, 3979, self.now))
        self.assertTrue(trade["tp2_hit"])
        self.assertEqual(trade["status"], "OPEN")

        self.assertTrue(self.lifecycle._apply_candle(trade, 3979, 3969, self.now))
        self.assertTrue(trade["tp3_hit"])
        self.assertEqual(trade["status"], "CLOSED")
        self.assertEqual(trade["outcome"], "TP3")

    def test_stop_before_any_target_is_terminal_sl(self):
        trade = self.trade("BUY")
        self.assertTrue(self.lifecycle._apply_candle(trade, 4002, 3989, self.now))
        self.assertEqual(trade["status"], "CLOSED")
        self.assertEqual(trade["outcome"], "SL")
        self.assertFalse(trade["tp1_hit"])

    def test_same_candle_target_and_stop_is_not_fabricated(self):
        trade = self.trade("BUY")
        changed = self.lifecycle._apply_candle(trade, 4011, 3989, self.now)

        self.assertFalse(changed)
        self.assertEqual(trade["status"], "OPEN")
        self.assertEqual(trade["outcome"], "PENDING")
        self.assertFalse(trade["tp1_hit"])
        self.assertEqual(
            trade["resolution_note"],
            "AMBIGUOUS_SAME_CANDLE_TARGET_AND_STOP",
        )

    def test_one_candle_can_record_multiple_targets_when_stop_not_touched(self):
        trade = self.trade("BUY")
        changed = self.lifecycle._apply_candle(trade, 4021, 4001, self.now)

        self.assertTrue(changed)
        self.assertTrue(trade["tp1_hit"])
        self.assertTrue(trade["tp2_hit"])
        self.assertFalse(trade["tp3_hit"])
        self.assertEqual(trade["status"], "OPEN")
        self.assertEqual(trade["outcome"], "PENDING")

        changed = self.lifecycle._apply_candle(trade, 4031, 4021, self.now)
        self.assertTrue(changed)
        self.assertTrue(trade["tp3_hit"])
        self.assertEqual(trade["status"], "CLOSED")
        self.assertEqual(trade["outcome"], "TP3")


if __name__ == "__main__":
    unittest.main()
