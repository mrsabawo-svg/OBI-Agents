import unittest
from unittest.mock import patch

from agents.edge_agent import EdgeAgent, MIN_SAMPLE
from agents.score_agent import ScoreAgent
from core.models import TriggerResult


def closed(symbol, regime, tags, outcome, grade="A"):
    return {
        "id": symbol + "_" + regime + "_" + str(tags) + "_" + outcome + "_" + grade,
        "symbol": symbol,
        "regime": regime,
        "tags": tags,
        "outcome": outcome,
        "status": "CLOSED",
        "grade": grade,
    }


class Pass10EdgeTest(unittest.TestCase):
    def setUp(self):
        self.archive = []

        # 20 XAUUSD/TRENDING/OB records: 15 wins, 5 losses.
        for i in range(15):
            self.archive.append(closed("XAUUSD", "TRENDING", ["OB"], "TP1", "A"))
        for i in range(5):
            self.archive.append(closed("XAUUSD", "TRENDING", ["OB"], "SL", "A"))

        # Additional independent history makes the broader symbol/base-rate
        # fallbacks observable without relying on current trigger grade.
        for i in range(10):
            self.archive.append(closed("XAUUSD", "RANGING", ["FVG"], "TP1", "B"))
        for i in range(10):
            self.archive.append(closed("EURUSD", "TRENDING", ["OB"], "SL", "C"))

    def trigger(self, grade):
        return TriggerResult(
            fire=True, direction="BUY", grade=grade,
            entry=4000.0, sl=3990.0, tp1=4010.0, tp2=4020.0,
            tp3=4030.0, rr=3.0, confluence=4, tags=["OB"], reason="test"
        )

    def test_grade_is_not_an_edge_lookup_key(self):
        with patch("agents.edge_agent.load_memory", return_value={"_archive": self.archive}):
            a = EdgeAgent("XAUUSD").analyse(
                self.trigger("A+"), None, {"label": "TRENDING"}
            )
            b = EdgeAgent("XAUUSD").analyse(
                self.trigger("C"), None, {"label": "TRENDING"}
            )

        self.assertEqual(a.lookup_level, "SYMBOL+REGIME+TAG")
        self.assertEqual(a.sample_size, MIN_SAMPLE)
        self.assertEqual(a.symbol_wr, b.symbol_wr)
        self.assertEqual(a.regime_wr, b.regime_wr)
        self.assertEqual(a.tag_wr, b.tag_wr)
        self.assertEqual(a.overall_wr, b.overall_wr)
        self.assertEqual(a.confidence_interval, b.confidence_interval)
        self.assertEqual(a.selected_wr, b.selected_wr)
        self.assertEqual(a.selected_wr, 75.0)
        self.assertFalse(a.low_sample)
        self.assertEqual(a.evidence_level, "E3")

    def test_hierarchical_fallback_uses_independent_features(self):
        archive = list(self.archive)
        # Only 10 matching-tag rows; symbol+regime has 20, so fallback
        # must select SYMBOL+REGIME rather than using a grade.
        archive = [x for x in archive if x["symbol"] != "XAUUSD" or x["regime"] != "TRENDING"]
        for i in range(20):
            archive.append(closed("XAUUSD", "TRENDING", ["Momentum"], "TP1" if i < 10 else "SL", "C"))
        with patch("agents.edge_agent.load_memory", return_value={"_archive": archive}):
            result = EdgeAgent("XAUUSD").analyse(
                self.trigger("A+"), None, {"label": "TRENDING"}
            )

        self.assertEqual(result.lookup_level, "SYMBOL+REGIME")
        self.assertEqual(result.sample_size, 20)
        self.assertEqual(result.selected_wr, 50.0)

    def test_low_sample_returns_neutral_prior(self):
        archive = self.archive[:MIN_SAMPLE - 1]
        with patch("agents.edge_agent.load_memory", return_value={"_archive": archive}):
            result = EdgeAgent("XAUUSD").analyse(
                self.trigger("A+"), None, {"label": "TRENDING"}
            )

        self.assertTrue(result.low_sample)
        self.assertEqual(result.sample_size, MIN_SAMPLE - 1)
        self.assertEqual(result.lookup_level, "BASE_RATE")
        self.assertEqual(result.selected_wr, 50.0)

    def test_score_does_not_depend_on_trigger_grade_through_edge(self):
        edge_a = EdgeAgent("XAUUSD")
        with patch("agents.edge_agent.load_memory", return_value={"_archive": self.archive}):
            edge = edge_a.analyse(self.trigger("A+"), None, {"label": "TRENDING"})

        from core.models import BiasResult
        from agents.score_agent import ScoreAgent

        bias = BiasResult(True, "BUY", "A", 6, ["HTF", "MTF"], "TRENDING", "test")
        session = {"kill_zone": True, "tradeable": True}

        # ScoreAgent receives identical edge evidence. Only trigger grade changes.
        score_a = ScoreAgent("XAUUSD").compute(
            bias, self.trigger("A+"), {"label": "TRENDING", "confidence": 1.0}, edge, session
        )
        score_b = ScoreAgent("XAUUSD").compute(
            bias, self.trigger("C"), {"label": "TRENDING", "confidence": 1.0}, edge, session
        )

        self.assertEqual(score_a.edge_score, score_b.edge_score)


    def test_edge_is_evidence_not_a_veto(self):
        from core.models import BiasResult, EdgeResult
        bias = BiasResult(True, "BUY", "A", 6, ["HTF", "MTF"], "TRENDING", "test")
        trigger = self.trigger("A+")
        session = {"kill_zone": False, "tradeable": True}
        weak_edge = EdgeResult(20.0, 20.0, 20.0, 20.0, 20.0, 40, False, (10.0, 35.0), "E3", "SYMBOL+REGIME+TAG")
        strong_edge = EdgeResult(80.0, 80.0, 80.0, 80.0, 80.0, 40, False, (65.0, 90.0), "E3", "SYMBOL+REGIME+TAG")
        weak = ScoreAgent("XAUUSD").compute(bias, trigger, {"label": "TRENDING", "confidence": 1.0}, weak_edge, session)
        strong = ScoreAgent("XAUUSD").compute(bias, trigger, {"label": "TRENDING", "confidence": 1.0}, strong_edge, session)
        self.assertLess(weak.edge_score, strong.edge_score)
        self.assertNotEqual(weak.confidence, strong.confidence)

    def test_final_grade_is_score_grade_not_trigger_grade(self):
        from core.models import BiasResult, ScoreResult
        from agents.archive_agent import ArchiveAgent
        bias = BiasResult(True, "BUY", "A", 6, ["HTF"], "TRENDING", "test")
        trigger = self.trigger("C")
        score = ScoreResult(88, "A+", "LOW", 80, 90, 100, 80, 60)
        payload = {"id": "XAUUSD_TEST_1", "symbol": "XAUUSD", "bias": bias, "trigger": trigger, "score": score, "regime": {"label": "TRENDING", "confidence": 1.0}, "htf": {}, "mtf": {}}
        entry = ArchiveAgent()._build_entry(payload)
        self.assertEqual(entry["grade"], "A+")
        self.assertEqual(entry["trigger_grade"], "C")

if __name__ == "__main__":
    unittest.main()
