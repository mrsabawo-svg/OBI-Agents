import unittest
from unittest.mock import patch
from datetime import datetime

from agents.archive_agent import ArchiveAgent
from agents.lifecycle_agent import LifecycleAgent, SAST
from agents.persistence_agent import PersistenceAgent


class FakeScore:
    confidence = 88
    grade = "A+"
    risk = "LOW"


class Pass12BLifecycleTest(unittest.TestCase):
    def test_signal_identity_grade_and_lifecycle_are_preserved(self):
        store = {"_archive": []}

        payload = {
            "id": "PASS12B_XAUUSD_001",
            "symbol": "XAUUSD",
            "score": FakeScore(),
            "regime": {"label": "TRENDING", "confidence": 90},
            "trigger": {
                "direction": "BUY",
                "grade": "C",
                "entry": 4000.0,
                "sl": 3990.0,
                "tp1": 4010.0,
                "tp2": 4020.0,
                "tp3": 4030.0,
                "rr": 3.0,
                "tags": ["OB"],
                "confluence": 4,
            },
            "bias": {"score": 80, "factors": ["HTF", "MTF"]},
            "htf": {"bias": "BULLISH", "confidence": 90},
            "mtf": {"bos": True, "sweep": False, "order_block": True},
        }

        def load():
            return store

        def save(memory):
            snapshot = dict(memory)
            snapshot["_archive"] = list(memory.get("_archive", []))
            store.clear()
            store.update(snapshot)

        # Archive creates exactly one canonical OPEN record.
        with patch("agents.archive_agent.load_memory", side_effect=load),              patch("agents.archive_agent.save_memory", side_effect=save):
            self.assertTrue(ArchiveAgent().log(payload))

        self.assertEqual(len(store["_archive"]), 1)
        trade = store["_archive"][0]
        self.assertEqual(trade["id"], payload["id"])
        self.assertEqual(trade["grade"], "A+")
        self.assertEqual(trade["trigger_grade"], "C")
        self.assertEqual(trade["status"], "OPEN")
        self.assertEqual(trade["outcome"], "PENDING")

        # Persistence updates symbol memory only; it cannot create another archive record.
        result = {
            "id": payload["id"],
            "timestamp": trade["timestamp"],
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
        with patch("agents.persistence_agent.load_memory", side_effect=load),              patch("agents.persistence_agent.save_memory", side_effect=save),              patch("agents.persistence_agent.requests.patch") as gist_patch:
            gist_patch.return_value.status_code = 200
            PersistenceAgent("XAUUSD").save(result, payload)

        self.assertEqual(len(store["_archive"]), 1)
        self.assertEqual(store["_archive"][0]["id"], payload["id"])

        # Lifecycle must close that exact record, not create a replacement.
        lifecycle = LifecycleAgent()
        now = SAST.localize(datetime.strptime(
            "2026-09-24 06:30", "%Y-%m-%d %H:%M"
        ))

        class CloseValues:
            def __init__(self, value):
                self.iloc = [value]

        class CloseSeries:
            def __init__(self, value):
                self.value = value

            def squeeze(self):
                return CloseValues(self.value)

        class FakeFrame:
            empty = False

            def __getitem__(self, key):
                if key != "Close":
                    raise AssertionError("Lifecycle requested unexpected column")
                return CloseSeries(4010.0)

        with patch("agents.lifecycle_agent.yf.download", return_value=FakeFrame()):
            memory = load()
            changed = lifecycle._check_trade(memory["_archive"][0], now)
            if changed:
                save(memory)

        self.assertTrue(changed)
        self.assertEqual(len(store["_archive"]), 1)
        closed = store["_archive"][0]
        self.assertEqual(closed["id"], payload["id"])
        self.assertEqual(closed["grade"], "A+")
        self.assertEqual(closed["trigger_grade"], "C")
        self.assertEqual(closed["status"], "CLOSED")
        self.assertEqual(closed["outcome"], "TP1")

        # Reload proves identity and lifecycle state survived persistence.
        final = load()
        self.assertEqual(len(final["_archive"]), 1)
        self.assertEqual(final["_archive"][0]["id"], payload["id"])
        self.assertEqual(final["_archive"][0]["status"], "CLOSED")
        self.assertEqual(final["_archive"][0]["outcome"], "TP1")


if __name__ == "__main__":
    unittest.main()
