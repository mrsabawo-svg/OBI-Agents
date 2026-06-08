"""
OBI Agents — Score Agent v4.2
Combines Bias + Trigger + Regime + Edge into one OBI confidence score.
Replaces AI-generated confidence with a computed 0-100 score.
"""

class ScoreAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def compute(self, bias: dict, trigger: dict, regime: dict, edge: dict, session: dict) -> dict:
        print("[SCORE] Computing OBI confidence for " + self.symbol)
        try:
            bias_score    = self._bias_score(bias)
            trigger_score = self._trigger_score(trigger)
            regime_score  = self._regime_score(regime)
            edge_score    = self._edge_score(edge)
            session_score = self._session_score(session)

            raw = (
                bias_score    * 0.30 +
                trigger_score * 0.30 +
                edge_score    * 0.25 +
                regime_score  * 0.15
            )

            confidence = min(100, max(0, round(raw * 100)))

            # Boost for kill zone
            if session.get("kill_zone"):
                confidence = min(100, confidence + 5)

            # Penalty for low sample size
            if edge.get("low_sample"):
                confidence = max(0, confidence - 10)

            grade = self._grade(confidence)
            risk  = self._risk(confidence, trigger.get("rr", 0))

            print("[SCORE] " + self.symbol + ": confidence=" + str(confidence) + " grade=" + grade + " risk=" + risk)

            return {
                "confidence":    confidence,
                "grade":         grade,
                "risk":          risk,
                "bias_score":    round(bias_score * 100),
                "trigger_score": round(trigger_score * 100),
                "regime_score":  round(regime_score * 100),
                "edge_score":    round(edge_score * 100),
            }

        except Exception as e:
            print("[SCORE] Error: " + str(e))
            return self._default()

    def _bias_score(self, bias: dict) -> float:
        score     = bias.get("score", 0)
        max_score = 7
        return min(1.0, score / max_score)

    def _trigger_score(self, trigger: dict) -> float:
        confluence = trigger.get("confluence", 0)
        rr         = trigger.get("rr", 0)
        conf_score = min(1.0, confluence / 5)
        rr_score   = min(1.0, rr / 5)
        return round((conf_score * 0.6 + rr_score * 0.4), 2)

    def _regime_score(self, regime: dict) -> float:
        label = regime.get("label", "RANGING")
        conf  = regime.get("confidence", 0.5)
        base  = {"TRENDING": 1.0, "RANGING": 0.5, "VOLATILE": 0.2}.get(label, 0.5)
        return round(base * conf, 2)

    def _edge_score(self, edge: dict) -> float:
        if edge.get("low_sample"):
            return 0.5
        symbol_wr = edge.get("symbol_wr", 50) / 100
        grade_wr  = edge.get("grade_wr", 50)  / 100
        regime_wr = edge.get("regime_wr", 50)  / 100
        return round((symbol_wr * 0.4 + grade_wr * 0.35 + regime_wr * 0.25), 2)

    def _session_score(self, session: dict) -> float:
        if session.get("kill_zone"):
            return 1.0
        if session.get("tradeable"):
            return 0.6
        return 0.0

    def _grade(self, confidence: int) -> str:
        if confidence >= 85:
            return "A+"
        elif confidence >= 75:
            return "A"
        elif confidence >= 65:
            return "B"
        elif confidence >= 55:
            return "C"
        else:
            return "D"

    def _risk(self, confidence: int, rr: float) -> str:
        if confidence >= 75 and rr >= 2:
            return "LOW"
        elif confidence >= 60 and rr >= 1.5:
            return "MEDIUM"
        else:
            return "HIGH"

    def _default(self) -> dict:
        return {"confidence": 50, "grade": "C", "risk": "HIGH",
                "bias_score": 50, "trigger_score": 50,
                "regime_score": 50, "edge_score": 50}
