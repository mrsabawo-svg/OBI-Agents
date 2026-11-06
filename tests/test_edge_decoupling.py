import unittest
from unittest.mock import patch
from agents.edge_agent import EdgeAgent
from core.models import TriggerResult

class TestEdgeDecoupling(unittest.TestCase):
    def test_trigger_grade_does_not_affect_edge_win_rate(self):
        archive = []
        for i in range(15):
            archive.append({
                "symbol": "XAUUSD",
                "status": "CLOSED",
                "outcome": "TP1",
                "grade": "A",
                "regime": "TRENDING",
                "tags": ["OB"]
            })
        for i in range(10):
            archive.append({
                "symbol": "XAUUSD",
                "status": "CLOSED",
                "outcome": "SL",
                "grade": "C",
                "regime": "TRENDING",
                "tags": ["OB"]
            })

        mock_memory = {"_archive": archive}

        trigger_c = TriggerResult(
            fire=True,
            direction="BUY",
            grade="C",
            entry=2000.0,
            sl=1990.0,
            tp1=2010.0,
            tp2=2020.0,
            tp3=2030.0,
            rr=2.0,
            confluence=3,
            tags=["OB"],
            reason="Test signal"
        )

        trigger_a = TriggerResult(
            fire=True,
            direction="BUY",
            grade="A",
            entry=2000.0,
            sl=1990.0,
            tp1=2010.0,
            tp2=2020.0,
            tp3=2030.0,
            rr=2.0,
            confluence=3,
            tags=["OB"],
            reason="Test signal"
        )

        regime = {"label": "TRENDING"}
        bias = None

        with patch("agents.edge_agent.load_memory", return_value=mock_memory):
            edge_agent = EdgeAgent("XAUUSD")
            result_c = edge_agent.analyse(trigger_c, bias, regime)
            result_a = edge_agent.analyse(trigger_a, bias, regime)

            # Both triggers must yield identical historical win-rate inputs
            # proving that changing trigger.grade cannot alter historical win rate.
            self.assertEqual(result_c.grade_wr, result_a.grade_wr)
            self.assertEqual(result_c.overall_wr, 60.0)
            self.assertEqual(result_c.grade_wr, 60.0)
            self.assertEqual(result_a.grade_wr, 60.0)

if __name__ == "__main__":
    unittest.main()
