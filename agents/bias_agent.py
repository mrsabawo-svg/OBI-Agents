"""
OBI Agents — Bias Agent
Combines HTF + MTF + Session into a unified directional bias.
This is the final gate before signal generation.
Minimum 3 confluence factors required to pass.
"""

class BiasAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def evaluate(self, htf: dict, mtf: dict, session: dict) -> dict:
        try:
            if not session.get("tradeable"):
                return self._blocked(f"Session: {session.get('reason')}")

            htf_bias   = htf.get("bias", "NEUTRAL")
            htf_conf   = htf.get("confidence", 0)
            mtf_align  = mtf.get("aligned", False)
            mtf_conf   = mtf.get("confluence", 0)
            in_kz      = session.get("kill_zone", False)

            # Hard block: no HTF direction
            if htf_bias == "NEUTRAL":
                return self._blocked("HTF bias is NEUTRAL — no directional edge")

            # Hard block: HTF confidence too low
            if htf_conf < 0.55:
                return self._blocked(f"HTF confidence too low ({htf_conf:.2f})")

            # Hard block: MTF not aligned
            if not mtf_align:
                return self._blocked("MTF structure not aligned with HTF bias")

            # Score confluence
            factors = {
                "HTF bias clear":       htf_conf >= 0.7,
                "MTF aligned":          mtf_align,
                "MTF BOS confirmed":    mtf.get("bos", False),
                "Liquidity sweep":      mtf.get("sweep", False),
                "Order block present":  mtf.get("order_block", False),
                "Kill zone active":     in_kz,
            }

            passed     = [k for k, v in factors.items() if v]
            score      = len(passed)
            direction  = mtf.get("direction", "NEUTRAL")

            # Minimum 3 confluence factors
            if score < 3:
                return self._blocked(f"Insufficient confluence ({score}/6 factors)")

            grade = "A" if score >= 5 else "B" if score >= 4 else "C"

            print(f"[BIAS] {self.symbol}: {direction} | Grade={grade} | Factors={score}/6")
            return {
                "approved":  True,
                "direction": direction,
                "grade":     grade,
                "score":     score,
                "factors":   passed,
                "reason":    f"{score}/6 confluence factors met"
            }

        except Exception as e:
            print(f"[BIAS] {self.symbol} error: {e}")
            return self._blocked("Bias evaluation failed")

    def _blocked(self, reason: str) -> dict:
        print(f"[BIAS] {self.symbol} BLOCKED: {reason}")
        return {"approved": False, "direction": "NEUTRAL",
                "grade": "F", "score": 0, "factors": [], "reason": reason}
