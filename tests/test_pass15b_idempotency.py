import unittest
from unittest.mock import MagicMock, patch

from agents.persistence_agent import PersistenceAgent
from agents.archive_agent import ArchiveAgent
from agents.lifecycle_agent import LifecycleAgent, SAST
from datetime import datetime


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
        self.assertEqual(len(memory["XAUUSD"]["_processed_signal_ids"]), 1)

    def test_save_skips_duplicate_external_snapshot_write(self):
        memory = {"XAUUSD": {"signals": 0, "wins": 0, "losses": 0}}
        agent = PersistenceAgent("XAUUSD")
        with patch("agents.persistence_agent.load_memory", return_value=memory), \
             patch("agents.persistence_agent.save_memory"), \
             patch.object(agent, "_push_to_gist") as push:
            agent.save(self.result, self.payload)
            agent.save(self.result, self.payload)
        self.assertEqual(push.call_count, 1)
        self.assertEqual(memory["XAUUSD"]["signals"], 1)

    def test_archive_repeated_log_is_idempotent(self):
        memory = {"_archive": []}
        signal = {
            "id": "PASS15B_ARCHIVE_001",
            "symbol": "XAUUSD",
            "trigger": {
                "direction": "BUY", "grade": "C", "entry": 4000.0,
                "sl": 3990.0, "tp1": 4010.0, "tp2": 4020.0,
                "tp3": 4030.0, "rr": 3.0, "tags": ["OB"], "confluence": 5,
            },
            "score": {"grade": "A+"},
            "regime": {"label": "TRENDING"},
            "bias": {}, "htf": {}, "mtf": {},
        }
        agent = ArchiveAgent()
        with patch("agents.archive_agent.load_memory", return_value=memory), \
             patch("agents.archive_agent.save_memory") as save:
            self.assertTrue(agent.log(signal))
            self.assertTrue(agent.log(signal))
        self.assertEqual(len(memory["_archive"]), 1)
        self.assertEqual(memory["_archive"][0]["id"], signal["id"])
        self.assertEqual(save.call_count, 1)

    def test_lifecycle_repeated_candle_is_idempotent(self):
        lifecycle = LifecycleAgent()
        now = SAST.localize(datetime.strptime("2026-09-24 07:00", "%Y-%m-%d %H:%M"))
        trade = {
            "id": "PASS15B_LIFE_001", "symbol": "XAUUSD", "direction": "BUY",
            "entry": 4000.0, "sl": 3990.0, "tp1": 4010.0, "tp2": 4020.0,
            "tp3": 4030.0, "status": "OPEN", "outcome": "PENDING",
            "tp1_hit": False, "tp2_hit": False, "tp3_hit": False,
        }
        self.assertTrue(lifecycle._apply_candle(trade, 4011, 4001, now))
        snapshot = dict(trade)
        self.assertFalse(lifecycle._apply_candle(trade, 4011, 4001, now))
        self.assertEqual(trade, snapshot)
        self.assertTrue(lifecycle._apply_candle(trade, 4031, 4021, now))
        self.assertEqual(trade["status"], "CLOSED")
        closed_snapshot = dict(trade)
        self.assertFalse(lifecycle._apply_candle(trade, 4031, 4021, now))
        self.assertEqual(trade, closed_snapshot)

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
