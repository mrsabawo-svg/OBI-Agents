"""
OBI Agents — Trigger Agent
Final entry confirmation.
"""
from core.models import TriggerResult

class TriggerAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def evaluate(self, ltf: dict, zone: dict, bias) -> TriggerResult:
        try:
            direction    = bias.direction
            ltf_valid    = ltf.get("valid", False)
            zone_aligned = zone.get("zone_aligned", False)
            rr           = ltf.get("rr", 0)
            confluence   = ltf.get("confluence", 0) + (1 if zone_aligned else 0)

            if not ltf_valid:
                return TriggerResult.blocked("LTF trigger not confirmed")

            if rr < 1.2:
                return TriggerResult.blocked("RR too low " + str(rr))

            if direction == "NEUTRAL":
                return TriggerResult.blocked("No directional bias")

            if not zone_aligned:
                print("[TRIGGER] " + self.symbol + ": zone not aligned but proceeding")

            grade = (
                "A+" if confluence >= 5 and rr >= 3 else
                "A"  if confluence >= 4 and rr >= 2 else
                "B"  if confluence >= 3 and rr >= 1.5 else "C"
            )

            tags = []
            if ltf.get("fvg"):        tags.append("FVG")
            if ltf.get("momentum"):   tags.append("Momentum")
            if zone.get("ob_bull") or zone.get("ob_bear"): tags.append("OB")
            if zone.get("in_discount") and direction == "BUY":  tags.append("Discount")
            if zone.get("in_premium")  and direction == "SELL": tags.append("Premium")

            print("[TRIGGER] " + self.symbol + ": FIRE " + direction + " Grade=" + grade + " RR=" + str(rr))
            return TriggerResult(
                fire=True,
                direction=direction,
                grade=grade,
                entry=ltf.get("entry", 0.0),
                sl=ltf.get("sl", 0.0),
                tp1=ltf.get("tp1", 0.0),
                tp2=ltf.get("tp2", 0.0),
                tp3=ltf.get("tp3", 0.0),
                rr=rr,
                confluence=confluence,
                tags=tags,
                reason="Grade " + grade + " setup"
            )

        except Exception as e:
            print("[TRIGGER] " + self.symbol + " error: " + str(e))
            return TriggerResult.blocked("Trigger evaluation failed")
