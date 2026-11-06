import unittest
from unittest.mock import patch
from agents.notifier_agent import NotifierAgent
from agents.signal_reviewer import SignalReviewer
from core.models import TriggerResult, ScoreResult, EdgeResult


class TestNotifierFormat(unittest.TestCase):
    def test_clean_telegram_message_format(self):
        trigger = TriggerResult(
            fire=True,
            direction="BUY",
            grade="A",
            entry=2040.50,
            sl=2030.00,
            tp1=2050.00,
            tp2=2060.00,
            tp3=2070.00,
            rr=2.0,
            confluence=4,
            tags=["Liquidity Sweep", "Bullish OB"],
            reason="Clean structural sweep"
        )
        score = ScoreResult(
            confidence=85,
            grade="A+",
            risk="LOW",
            bias_score=80,
            trigger_score=90,
            regime_score=85,
            edge_score=75,
            session_score=100
        )
        edge = EdgeResult(
            symbol_wr=65.0,
            grade_wr=62.0,
            regime_wr=60.0,
            tag_wr=64.0,
            overall_wr=62.0,
            sample_size=35,
            low_sample=False
        )

        payload = {
            "symbol": "XAUUSD",
            "trigger": trigger,
            "score": score,
            "edge": edge,
            "regime": {"label": "TRENDING", "confidence": 0.85},
            "session": {"kill_zone": True, "tradeable": True},
            "timestamp": "2026-11-06 14:15:00 SAST"
        }

        reviewer = SignalReviewer("XAUUSD")
        narrative = reviewer.build_narrative(payload)

        # Assert narrative contains concise SETUP and TRAP / WATCH lines
        self.assertIn("SETUP: Kill Zone + TRENDING Regime (Liquidity Sweep + Bullish OB)", narrative)
        self.assertIn("TRAP / WATCH:", narrative)
        self.assertNotIn("Groq", narrative)
        self.assertNotIn("Skeptic", narrative)

        # Assert notifier formats message cleanly without ANALYST / SKEPTIC offline sections
        notifier = NotifierAgent("XAUUSD")
        result_dict = {
            "symbol": "XAUUSD",
            "direction": "BUY",
            "confluence": 4,
            "entry": 2040.50,
            "sl": 2030.00,
            "tp1": 2050.00,
            "tp2": 2060.00,
            "tp3": 2070.00,
            "rr": 2.0,
            "tags": ["Liquidity Sweep", "Bullish OB"],
            "score": score,
            "timestamp": "2026-11-06 14:15:00 SAST"
        }

        with patch("agents.notifier_agent.requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            notifier.send(result_dict, narrative)
            self.assertTrue(mock_post.called)
            sent_text = mock_post.call_args[1]["json"]["text"]

            self.assertIn("OBI SIGNAL - XAUUSD [Grade: A+ | Risk: LOW]", sent_text)
            self.assertIn("Direction: BUY | Confluence: 4", sent_text)
            self.assertIn("Entry: 2040.5", sent_text)
            self.assertIn("SETUP: Kill Zone + TRENDING Regime", sent_text)
            self.assertIn("TRAP / WATCH:", sent_text)
            self.assertNotIn("ANALYST:", sent_text)
            self.assertNotIn("SKEPTIC:", sent_text)
            self.assertNotIn("Groq unavailable", sent_text)


if __name__ == "__main__":
    unittest.main()
