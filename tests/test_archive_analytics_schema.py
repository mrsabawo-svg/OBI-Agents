import unittest

from agents.archive_agent import ArchiveAgent
from agents.factor_agent import FactorAgent


class FakeScore:
    confidence = 88
    grade = "A+"


class ArchiveAnalyticsSchemaTest(unittest.TestCase):
    def _signal(self, score):
        return {
            "id": "M1_SCHEMA_TEST",
            "symbol": "XAUUSD",
            "score": score,
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
            "htf": {},
            "mtf": {},
        }

    def test_archive_preserves_factor_agent_schema_for_score_object(self):
        entry = ArchiveAgent()._build_entry(self._signal(FakeScore()))

        self.assertEqual(entry["factors"], ["HTF", "MTF"])
        self.assertEqual(entry["bias_factors"], ["HTF", "MTF"])
        self.assertEqual(entry["obi_score"], 88)
        self.assertEqual(entry["grade"], "A+")
        self.assertEqual(entry["trigger_grade"], "C")

    def test_archive_preserves_factor_agent_schema_for_score_dict(self):
        entry = ArchiveAgent()._build_entry(
            self._signal({"confidence": 73, "grade": "A"})
        )

        self.assertEqual(entry["factors"], ["HTF", "MTF"])
        self.assertEqual(entry["obi_score"], 73)
        self.assertEqual(entry["grade"], "A")

    def test_factor_agent_consumes_restored_fields(self):
        agent = FactorAgent()
        closed = [
            {
                "status": "CLOSED",
                "outcome": "TP1" if index < 4 else "SL",
                "factors": ["HTF", "MTF"],
                "obi_score": 88,
            }
            for index in range(5)
        ]

        by_factor = agent._by_factor(closed)
        by_score = agent._by_score_bucket(closed)

        self.assertEqual(by_factor["HTF"], {"wr": 80.0, "n": 5})
        self.assertEqual(by_factor["MTF"], {"wr": 80.0, "n": 5})
        self.assertEqual(by_score["70+"], {"wr": 80.0, "n": 5})


if __name__ == "__main__":
    unittest.main()
