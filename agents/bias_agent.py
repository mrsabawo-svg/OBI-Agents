"""
OBI Agents — Bias Agent
Combines HTF + MTF + Session into unified directional bias.
"""

class BiasAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def evaluate(self, htf: dict, mtf: dict, session: dict) -> dict:
        try:
            if not session.get("tradeable"):
                return self._blocked(f"Session: {session.get('reason')}")

            htf_bias  = htf.get("bias", "NEUTRAL")
            htf_conf  = htf.get("confidence", 0)
            mtf_align = mtf.get("aligned", False)
            in_kz     = session.get("kill_zone", False)

            if htf_bias == "NEUTRAL":
                return self._blocked("HTF bias is NEUTRAL — no directional edge")

            if htf_conf < 0.50:
                return self._blocked(f"HTF confidence too low ({htf_conf:.2f})")

            if not mtf_align:
                return self._blocked("MTF structure not aligned with HTF bias")

            # Count confluence — lowered thresholds
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

            # Lowered minimum from 3 to 2
            if score < 2:
                return self._blocked(f"Insufficient confluence ({score}/6 factors)")

            grade = "A" if score >= 5 else "B" if score >= 4 else "C" if score >= 3 else "D"

            print(f"[BIAS] {self.symbol}: {direction} | Grade
