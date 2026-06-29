"""
OBI Agents — Bias Agent
"""
from core.models import BiasResult

class BiasAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def evaluate(self, htf: dict, mtf: dict, session: dict, regime: dict = None) -> BiasResult:
        try:
            if not session.get("tradeable"):
                return BiasResult.blocked("Session: " + str(session.get("reason")))

            htf_bias  = htf.get("bias", "NEUTRAL")
            htf_conf  = htf.get("confidence", 0)
            mtf_align = mtf.get("aligned", False)
            in_kz     = session.get("kill_zone", False)

            regime_label = regime.get("label", "RANGING") if regime else "RANGING"
            regime_conf  = regime.get("confidence", 0) if regime else 0

            if htf_bias == "NEUTRAL":
                return BiasResult.blocked("HTF bias is NEUTRAL — no directional edge")

            if htf_conf < 0.50:
                return BiasResult.blocked("HTF confidence too low")

            if not mtf_align:
                return BiasResult.blocked("MTF structure not aligned with HTF bias")

            if regime_label == "VOLATILE" and regime_conf > 0.7:
                return BiasResult.blocked("HMM: High volatility regime — too risky")

            factors = {
                "HTF bias clear":      htf_conf >= 0.60,
                "MTF aligned":         mtf_align,
                "MTF BOS confirmed":   mtf.get("bos", False),
                "Liquidity sweep":     mtf.get("sweep", False),
                "Order block present": mtf.get("order_block", False),
                "Kill zone active":    in_kz,
                "Trending regime":     regime_label == "TRENDING" and regime_conf >= 0.6,
            }

            passed    = [k for k, v in factors.items() if v]
            score     = len(passed)
            direction = mtf.get("direction", "NEUTRAL")

            if score < 2:
                return BiasResult.blocked("Insufficient confluence (" + str(score) + "/7 factors)")

            grade = (
                "A+" if score >= 6 else
                "A"  if score >= 5 else
                "B"  if score >= 4 else
                "C"  if score >= 3 else "D"
            )

            print("[BIAS] " + self.symbol + ": " + direction + " Grade=" + grade + " Factors=" + str(score) + "/7 Regime=" + regime_label)
            return BiasResult(
                approved=True,
                direction=direction,
                grade=grade,
                score=score,
                factors=passed,
                regime=regime_label,
                reason=str(score) + "/7 confluence factors met"
            )

        except Exception as e:
            print("[BIAS] " + self.symbol + " error: " + str(e))
            return BiasResult.blocked("Bias evaluation failed")
