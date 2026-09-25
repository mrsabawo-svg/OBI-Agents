import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from agents.signal_reviewer import SignalReviewer, SAST


class Pass16IdentityTest(unittest.TestCase):
    def setUp(self):
        self.trigger = {
            "direction": "BUY",
            "entry": 4000.0,
            "sl": 3990.0,
            "tp1": 4010.0,
            "tp2": 4020.0,
            "tp3": 4030.0,
            "tags": ["OB"],
        }

    def payload(self, signal_id):
        return {
            "id": signal_id,
            "symbol": "XAUUSD",
            "trigger": dict(self.trigger),
            "regime": {"label": "TRENDING"},
        }

    def opened(self, hours_ago):
        return (datetime.now(SAST) - timedelta(hours=hours_ago)).strftime(
            "%Y-%m-%d %H:%M SAST"
        )

    def test_signal_identity_and_setup_identity_are_distinct(self):
        first = self.payload("SIGNAL_001")
        second = self.payload("SIGNAL_002")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(SignalReviewer.setup_identity(first),
                         SignalReviewer.setup_identity(second))

    def test_material_setup_change_gets_new_setup_identity(self):
        first = self.payload("SIGNAL_001")
        second = self.payload("SIGNAL_002")
        second["trigger"]["tp2"] = 4025.0
        self.assertNotEqual(SignalReviewer.setup_identity(first),
                            SignalReviewer.setup_identity(second))

    def test_exact_signal_id_is_duplicate(self):
        payload = self.payload("SIGNAL_001")
        archive = [{
            "id": "SIGNAL_001", "symbol": "XAUUSD", "status": "OPEN",
            "opened": self.opened(1), "setup_id": "different-setup",
        }]
        with patch("agents.signal_reviewer.load_memory",
                   return_value={"_archive": archive}):
            self.assertTrue(SignalReviewer("XAUUSD").is_duplicate(payload))

    def test_same_setup_is_duplicate_even_with_new_signal_id(self):
        first = self.payload("SIGNAL_001")
        second = self.payload("SIGNAL_002")
        archive = [{
            "id": first["id"], "symbol": "XAUUSD", "status": "OPEN",
            "opened": self.opened(1),
            "setup_id": SignalReviewer.setup_identity(first),
        }]
        with patch("agents.signal_reviewer.load_memory",
                   return_value={"_archive": archive}):
            self.assertTrue(SignalReviewer("XAUUSD").is_duplicate(second))

    def test_old_setup_is_not_blocked_after_window(self):
        payload = self.payload("SIGNAL_002")
        archive = [{
            "id": "SIGNAL_001", "symbol": "XAUUSD", "status": "OPEN",
            "opened": self.opened(3),
            "setup_id": SignalReviewer.setup_identity(payload),
        }]
        with patch("agents.signal_reviewer.load_memory",
                   return_value={"_archive": archive}):
            self.assertFalse(SignalReviewer("XAUUSD").is_duplicate(payload))


if __name__ == "__main__":
    unittest.main()
