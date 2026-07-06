"""
OBI Agents - Score Agent v4.3
Unified OBI confidence score 0-100.

Recalibration changes vs v4.2:
- RR cap lowered from 5 to 3 (RR>3 is rare, was wasting score range)
- Regime score: RANGING raised from 0.5 to 0.7 base (most signals are ranging)
- Low sample: single -8 penalty only (removed double-hit from 0.5 floor + -10 flat)
- Edge floor when low sample: 0.4 instead of 0.5 (slightly more conservative)
- Net effect: weak signals stay D, strong signals can now reach A/A+
"""
from core.models import BiasResult, TriggerResult, EdgeResult, ScoreResult


class ScoreAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def compute(self, bias: BiasResult, trigger: TriggerResult, regime: dict, edge: EdgeResult, session: dict) -> ScoreResult:
        print("[SCORE] Computing OBI confidence for " + self.symbol)
        try:
            bias_score    = self._bias_score(bias)
            trigger_score = self._trigger_score(trigger)
            regime_score  = self._regime_score(regime)
            edge_score    = self._edge_score(edge)
            session_score = self._session_score(session)

            raw = (
                bias_score    * 0.28 +
                trigger_score * 0.28 +
                edge_score    * 0.24 +
                regime_score  * 0.10 +
                session_score * 0.10
            )

            confidence = min(100, max(0, round(raw * 100)))

            # Single bonuses / penalties — no double counting
            if session.get("kill_zone"):
                confidence = min(100, confidence + 5)

            if edge.low_sample:
                confidence = max(0, confidence - 8)

            grade = self._grade(confidence)
            risk  = self._risk(confidence, trigger.rr)

            print("[SCORE] " + self.symbol + ": confidence=" + str(confidence) + " grade=" + grade + " risk=" + risk)

            return ScoreResult(
                confidence=confidence,
                grade=grade,
                risk=risk,
                bias_score=round(bias_score * 100),
                trigger_score=round(trigger_score * 100),
                regime_score=round(regime_score * 100),
                edge_score=round(edge_score * 100),
                session_score=round(session_score * 100),
            )

        except Exception as e:
            print("[SCORE] Error: " + str(e))
            return ScoreResult.default()

    def _bias_score(self, bias: BiasResult) -> float:
        return min(1.0, bias.score / 7)

    def _trigger_score(self, trigger: TriggerResult) -> float:
        conf_score = min(1.0, trigger.confluence / 5)
        rr_score   = min(1.0, trigger.rr / 3)   # cap at RR=3, not 5
        return round(conf_score * 0.6 + rr_score * 0.4, 2)

    def _regime_score(self, regime: dict) -> float:
        label = regime.get("label", "RANGING")
        conf  = regime.get("confidence", 0.5)
        base  = {"TRENDING": 1.0, "RANGING": 0.7, "VOLATILE": 0.1}.get(label, 0.5)
        return round(base * conf, 2)

    def _edge_score(self, edge: EdgeResult) -> float:
        if edge.low_sample:
            return 0.4  # conservative neutral — single penalty applied later
        return round(edge.symbol_wr * 0.4 + edge.grade_wr * 0.35 + edge.regime_wr * 0.25, 2) / 100

    def _session_score(self, session: dict) -> float:
        if session.get("kill_zone"):  return 1.0
        if session.get("tradeable"):  return 0.6
        return 0.0

    def _grade(self, confidence: int) -> str:
        if confidence >= 85:   return "A+"
        elif confidence >= 75: return "A"
        elif confidence >= 65: return "B"
        elif confidence >= 55: return "C"
        else:                  return "D"

    def _risk(self, confidence: int, rr: float) -> str:
        if confidence >= 75 and rr >= 2:     return "LOW"
        elif confidence >= 60 and rr >= 1.5: return "MEDIUM"
        else:                                return "HIGH"

    def _default(self) -> ScoreResult:
        return ScoreResult.default()
