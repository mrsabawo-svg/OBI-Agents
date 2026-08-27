import unittest
from unittest.mock import patch

from agents.edge_agent import EdgeAgent
from core.models import TriggerResult


class TestEdgeGradeLoop(unittest.TestCase):
    def _trigger(self, grade):
        return TriggerResult(
            fire=True, direction="BUY", grade=grade,
            entry=1.0, sl=0.9, tp1=1.1, tp2=1.2, tp3=1.3,
            rr=2.0, confluence=4, tags=["FVG"], reason="test"
        )

    def test_edge_changes_when_only_trigger_grade_changes(self):
        archive = [
            {"symbol":"XAUUSD","status":"CLOSED","outcome":"TP1","grade":"A","regime":"TRENDING","tags":["FVG"]}
            for _ in range(10)
        ] + [
            {"symbol":"XAUUSD","status":"CLOSED","outcome":"SL","grade":"C","regime":"TRENDING","tags":["FVG"]}
            for _ in range(10)
        ]

        with patch("agents.edge_agent.load_memory", return_value={"_archive": archive}):
            edge = EdgeAgent("XAUUSD")
            result_a = edge.analyse(self._trigger("A"), None, {"label":"TRENDING"})
            result_c = edge.analyse(self._trigger("C"), None, {"label":"TRENDING"})

        self.assertNotEqual(result_a, result_c)
        self.assertEqual(result_a.grade_wr, 100.0)
        self.assertEqual(result_c.grade_wr, 0.0)


if __name__ == "__main__":
    unittest.main()
