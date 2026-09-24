import unittest
from unittest.mock import MagicMock, patch

from agents.persistence_agent import PersistenceAgent


class Score:
    confidence = 88


class Pass15BIdempotencyTest(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "id": "PASS15B_XAUUSD_001",
            "score": Score(),
            "regime": {"label": "TRENDING"},
        }
        self.result = {
            "id": self.payload["id"],
            "timestamp": "2026-09-24 07:00 SAST",
            "direction": "BUY",
            "grade": "A+",
            "entry": 4000.0,
            "sl": 3990.0,
            "tp1": 4010.0,
            "tp2": 4020.0,
            "tp3": 4030.0,
            "rr": 3.0,
            "tags": ["OB"],
        }

    def test_reprocessing_same_signal_does_not_increment_state(self):
        memory = {"XAUUSD": {"signals": 0, "wins": 0, "losses": 0}}

        agent = PersistenceAgent("XAUUSD")
        with patch("agents.persistence_agent.save_memory") as save:
            agent._update_memory(memory, self.result, self.payload)
            first_snapshot = dict(memory["XAUUSD"])
            agent._update_memory(memory, self.result, self.payload)

        self.assertEqual(memory["XAUUSD"]["signals"], 1)
        self.assertEqual(memory["XAUUSD"]["last_signal_data"]["id"], self.payload["id"])
        self.assertEqual(memory["XAUUSD"]["_processed_signal_ids"], [self.payload["id"]])
        self.assertEqual(memory["XAUUSD"], first_snapshot)
        self.assertEqual(save.call_count, 1)

    def test_different_signal_ids_are_each_processed_once(self):
        memory = {"XAUUSD": {"signals": 0, "wins": 0, "losses": 0}}
        agent = PersistenceAgent("XAUUSD")

        second = dict(self.result)
        second["id"] = "PASS15B_XAUUSD_002"

        with patch("agents.persistence_agent.save_memory"):
            agent._update_memory(memory, self.result, self.payload)
            agent._update_memory(memory, second, self.payload)
            agent._update_memory(memory, second, self.payload)

        self.assertEqual(memory["XAUUSD"]["signals"], 2)
        self.assertEqual(
            memory["XAUUSD"]["_processed_signal_ids"],
            ["PASS15B_XAUUSD_001", "PASS15B_XAUUSD_002"],
        )


if __name__ == "__main__":
    unittest.main()
