"""
OBI Agents — Trigger Agent
Final entry confirmation. Combines LTF + Zone into
a precise entry decision with full trade parameters.
"""

class TriggerAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def evaluate(self, ltf: dict, zone: dict, bias: dict) -> dict:
        try:
            direction    = bias.get("direction", "NEUTRAL")
            ltf_valid    = ltf.get("valid", False)
            zone_aligned = zone.get("zone_aligned", False)
            rr           = ltf.get("rr", 0)
            confluence   = ltf.get("confluence", 0) + (1 if zone_aligned else 0)

            # Hard blocks
            if not ltf_valid:
                return self._blocked("LTF trigger not confirmed")
            if not zone_aligned:
                return self._blocked("Price not in correct zone for direction")
            if rr < 1.5:
                return self._blocked(f"RR too low ({rr} < 1.5)")
            if direction == "NEUTRAL":
                return self._blocked("No directional bias")

            # Grade the setup
            grade = "A+" if confluence >= 5 and rr >= 3 else \
                    "A"  if confluence >= 4 and rr >= 2 else \
                    "B"  if confluence >= 3 and rr >= 1.5 else "C"

            tags = []
            if ltf.get("fvg"):        tags.append("FVG")
            if ltf.get("momentum"):   tags.append("Momentum")
            if zone.get("ob_bull") or zone.get("ob_bear"): tags.append("OB")
            if zone.get("in_discount") and direction == "BUY":  tags.append("Discount")
            if zone.get("in_premium")  and direction == "SELL": tags.append("Premium")

            print(f"[TRIGGER] {self.symbol}: FIRE {direction} | Grade={grade} | RR={rr} | Tags={tags}")
            return {
                "fire":       True,
                "direction":  direction,
                "grade":      grade,
                "entry":      ltf.get("entry"),
                "sl":         ltf.get("sl"),
                "tp1":        ltf.get("tp1"),
                "tp2":        ltf.get("tp2"),
                "tp3":        ltf.get("tp3"),
                "rr":         rr,
                "confluence": confluence,
                "tags":       tags,
                "reason":     f"Grade {grade} setup — {confluence} confluence factors"
            }

        except Exception as e:
            print(f"[TRIGGER] {self.symbol} error: {e}")
            return self._blocked("Trigger evaluation failed")

    def _blocked(self, reason: str) -> dict:
        print(f"[TRIGGER] {self.symbol} NO FIRE: {reason}")
        return {"fire": False, "direction": "NEUTRAL", "grade": "F",
                "entry": 0, "sl": 0, "tp1": 0, "tp2": 0, "tp3": 0,
                "rr": 0, "confluence": 0, "tags": [], "reason": reason}
