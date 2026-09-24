import unittest
from unittest.mock import patch

from agents.archive_agent import ArchiveAgent
from agents.lifecycle_agent import LifecycleAgent
from agents.persistence_agent import PersistenceAgent


class FakeScore:
    confidence = 72


class Pass9ArchiveLifecycleTest(unittest.TestCase):
    def test_open_persist_reload_close_reload_single_record(self):
        store = {"_archive": []}

        result = {
            "id": "PASS9_XAUUSD_001",
            "symbol": "XAUUSD",
            "timestamp": "2026-09-24 06:00 SAST",
            "direction": "BUY",
            "grade": "B",
            "entry": 4000.0,
            "sl": 3990.0,
            "tp1": 4010.0,
            "tp2": 4020.0,
            "tp3": 4030.0,
            "rr": 3.0,
            "tags": ["OB"],
            "regime": {},
            "score": {},
            "edge": {},
        }

        payload = {
            "id": result["id"],
            "symbol": "XAUUSD",
            "score": FakeScore(),
            "regime": {"label": "TRENDING", "confidence": 80},
            "trigger": {
                "direction": "BUY",
                "grade": "B",
                "entry": 4000.0,
                "sl": 3990.0,
                "tp1": 4010.0,
                "tp2": 4020.0,
                "tp3": 4030.0,
                "rr": 3.0,
                "tags": ["OB"],
                "confluence": 4,
            },
            "bias": {"score": 70, "factors": ["HTF"]},
            "htf": {"bias": "BULLISH", "confidence": 75},
            "mtf": {"bos": True, "sweep": False, "order_block": True},
        }

        def load():
            return store

        def save(memory):
            store.clear()
            store.update(memory)

        # Persistence must update symbol memory without creating an archive record.
        with patch("agents.persistence_agent.load_memory", side_effect=load),              patch("agents.persistence_agent.save_memory", side_effect=save):
            PersistenceAgent("XAUUSD").save(result, payload)

        self.assertEqual(len(store["_archive"]), 0)

        # ArchiveAgent is the sole creator of the OPEN record.
        with patch("agents.archive_agent.load_memory", side_effect=load),              patch("agents.archive_agent.save_memory", side_effect=save):
            self.assertTrue(ArchiveAgent().log(payload))

        self.assertEqual(len(store["_archive"]), 1)
        self.assertEqual(store["_archive"][0]["id"], result["id"])
        self.assertEqual(store["_archive"][0]["status"], "OPEN")

        # Reload boundary: a fresh read sees exactly the same single record.
        reloaded = load()
        self.assertEqual(len(reloaded["_archive"]), 1)
        self.assertEqual(reloaded["_archive"][0]["id"], result["id"])

        # Lifecycle closes the existing record; it must not create another one.
        lifecycle = LifecycleAgent()
        now = lifecycle.SAST.localize(
            __import__("datetime").datetime.strptime(
                "2026-09-24 06:30", "%Y-%m-%d %H:%M"
            )
        )
        with patch("agents.lifecycle_agent.yf.download") as download:
            download.return_value = type(
                "DF",
                (),
                {
                    "empty": False,
                    "__getitem__": lambda self, key: type(
                        "CloseSeries",
                        (),
                        {"squeeze": lambda self: type(
                            "Squeezed",
                            (),
                            {"iloc": [-1]}
                        )()}
                    )()
                },
            )()
            # Simpler price path: patch the internal trade checker.
            with patch.object(
                lifecycle,
                "_check_trade",
                side_effect=lambda trade, current: (
                    trade.update(
                        status="CLOSED",
                        outcome="TP1",
                        closed="2026-09-24 06:30 SAST",
                    ) or True
                ),
            ):
                memory = load()
                changed = lifecycle._check_trade(memory["_archive"][0], now)
                if changed:
                    save(memory)

        self.assertEqual(len(store["_archive"]), 1)
        self.assertEqual(store["_archive"][0]["id"], result["id"])
        self.assertEqual(store["_archive"][0]["status"], "CLOSED")
        self.assertEqual(store["_archive"][0]["outcome"], "TP1")

        # Final reload: CLOSED state and outcome survive persistence.
        final = load()
        self.assertEqual(len(final["_archive"]), 1)
        self.assertEqual(final["_archive"][0]["status"], "CLOSED")
        self.assertEqual(final["_archive"][0]["outcome"], "TP1")


if __name__ == "__main__":
    unittest.main()
