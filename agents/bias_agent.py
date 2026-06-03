"""
OBI Agents — Bias Agent
"""

class BiasAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def evaluate(self, htf: dict, mtf: dict, session: dict) -> dict:
        try:
            if not session.get("tradeable"):
                return self._blocked("Session: " + str(session.get("reason")))

            htf_bias  = htf.get("bias", "NEUTRAL")
            htf_conf  = htf.get("confidence", 0)
            mtf_align = mtf.get("aligned", False)
            in_kz     = session.get("kill_zone", False)

            if htf_bias == "NEUTRAL":
                return self._blocked("HTF bias is NEUTRAL — no directional edge")

            if htf_conf < 0.50:
                return self._blocked("HTF confidence too low")

            if not mtf_align:
                return self._blocked("MTF structure not aligned with HTF bias")

            factors = {
                "HTF bias clear":      htf_conf >= 0.60,
                "MTF aligned":         mtf_align,
                "MTF BOS confirmed":   mtf.get("bos", False),
                "Liquidity sweep":     mtf.get("sweep", False),
                "Order block present": mtf.get("order_block", False),
                "Kill zone active":    in_kz,
            }

            passed    = [k for k, v in factors.items() if v]
            score     = len(passed)
            direction = mtf.get("direction", "NEUTRAL")

            if score < 2:
                return self._blocked("Insufficient confluence (" + str(score) + "/6 factors)")

            if score >= 5:
                grade = "A"
            elif score >= 4:
                grade = "B"
            elif score >= 3:
                grade = "C"
            else:
                grade = "D"

            print("[BIAS] " + self.symbol + ": " + direction + " Grade=" + grade + " Factors=" + str(score) + "/6")
            return {
                "approved":  True,
                "direction": direction,
                "grade":     grade,
                "score":     score,
                "factors":   passed,
                "reason":    str(score) + "/6 confluence factors met"
            }

        except Exception as e:
            print("[BIAS] " + self.symbol + " error: " + str(e))
            return self._blocked("Bias evaluation failed")

    def _blocked(self, reason: str) -> dict:
        print("[BIAS] " + self.symbol + " BLOCKED: " + reason)
        return {"approved": False, "direction": "NEUTRAL",
                "grade": "F", "score": 0, "factors": [], "reason": reason}
