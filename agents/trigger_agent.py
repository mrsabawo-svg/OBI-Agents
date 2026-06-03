"""
OBI Agents — Trigger Agent
Final entry confirmation.
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

            if not ltf_valid:
                return self._blocked("LTF trigger not confirmed")

            if rr < 1.2:
    return self._blocked("RR too low " + str(rr))


            if direction == "NEUTRAL":
                return self._blocked("No directional bias")

            if not zone_aligned:
                print("[TRIGGER] " + self.symbol + ": zone not aligned but proceeding")

            if score >= 5 and rr >= 3:
                grade = "A+"
            elif confluence >= 4 and rr >= 2:
                grade = "A"
            elif confluence >= 3 and rr >= 1.5:
                grade = "B"
            else:
                grade = "C"

            tags = []
            if ltf.get("fvg"):      tags.append("FVG")
            if ltf.get("momentum"): tags.append("Momentum")
            if zone.get("ob_bull") or zone.get("ob_bear"): tags.append("OB")
            if zone.get("in_discount") and direction == "BUY":  tags.append("Discount")
            if zone.get("in_premium")  and direction == "SELL": tags.append("Premium")

            print("[TRIGGER] " + self.symbol + ": FIRE " + direction + " Grade=" + grade + " RR=" + str(rr))
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
                "reason":     "Grade " + grade + " setup"
            }

        except Exception as e:
            print("[TRIGGER] " + self.symbol + " error: " + str(e))
            return self._blocked("Trigger evaluation failed")

    def _blocked(self, reason: str) -> dict:
        print("[TRIGGER] " + self.symbol + " NO FIRE: " + reason)
        return {"fire": False, "direction": "NEUTRAL", "grade": "F",
                "entry": 0, "sl": 0, "tp1": 0, "tp2": 0, "tp3": 0,
                "rr": 0, "confluence": 0, "tags": [], "reason": reason}
