import unittest
from unittest.mock import MagicMock, patch

from agents.archive_agent import ArchiveAgent
from agents.tracker_agent import check_outcome


class Pass13ArchitectureTest(unittest.TestCase):
    def test_tracker_is_only_a_compatibility_adapter(self):
        with patch("agents.tracker_agent.LifecycleAgent") as lifecycle_cls:
            check_outcome("XAUUSD", "ignored")
        lifecycle_cls.return_value.check_open_signals.assert_called_once()

    def test_archive_has_no_competing_lifecycle_update_api(self):
        self.assertFalse(hasattr(ArchiveAgent, "update_outcome"))

    def test_lifecycle_owns_existing_record_mutation(self):
        from agents.lifecycle_agent import LifecycleAgent
        trade = {
            "id": "PASS13_XAUUSD_001",
            "symbol": "XAUUSD",
            "direction": "BUY",
            "entry": 4000.0,
            "sl": 3990.0,
            "tp1": 4010.0,
            "tp2": 4020.0,
            "tp3": 4030.0,
            "status": "OPEN",
            "opened": "2026-09-24 06:00 SAST",
        }

        class CloseValues:
            def __init__(self, value):
                self.iloc = [value]

        class CloseSeries:
            def squeeze(self):
                return CloseValues(4010.0)

        class FakeFrame:
            empty = False
            def __getitem__(self, key):
                self.assert_key = key
                return CloseSeries()

        with patch("agents.lifecycle_agent.yf.download", return_value=FakeFrame()):
            from agents.lifecycle_agent import SAST
            from datetime import datetime
            now = SAST.localize(datetime.strptime(
                "2026-09-24 06:30", "%Y-%m-%d %H:%M"
            ))
            changed = LifecycleAgent()._check_trade(trade, now)

        self.assertTrue(changed)
        self.assertEqual(trade["status"], "CLOSED")
        self.assertEqual(trade["outcome"], "TP1")
        self.assertEqual(trade["id"], "PASS13_XAUUSD_001")


if __name__ == "__main__":
    unittest.main()
