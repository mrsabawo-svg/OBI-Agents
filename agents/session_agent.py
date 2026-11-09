""" 
OBI Agents — Session Agent
Determines which trading session is active (SAST).
Only fires signals during high-probability kill zones.
"""
from core.utils import now_sast, is_kill_zone

CRYPTO_SYMBOLS    = {"BTCUSD", "ETHUSD", "SOLUSD"}
SYNTHETIC_SYMBOLS = {"PAIN_999", "GAIN_999", "PAIN_1200", "GAIN_1200", "SFX_VOL_75"}

SYMBOL_SESSIONS = {
    "XAUUSD":      ["London Open", "New York Open"],
    "EURUSD":      ["London Open", "New York Open"],
    "USDJPY":      ["London Open", "New York Open", "Asian Session"],
    "GBPJPY":      ["London Open", "New York Open"],
    "GBPUSD":      ["London Open", "New York Open"],
    "NASDAQ":      ["New York Open"],
    "BTCUSD":      ["London Open", "New York Open", "Asian Session"],
    "ETHUSD":      ["London Open", "New York Open", "Asian Session"],
    "SOLUSD":      ["London Open", "New York Open", "Asian Session"],
    "PAIN_999":    ["London Open", "New York Open", "Asian Session", "London Close"],
    "GAIN_999":    ["London Open", "New York Open", "Asian Session", "London Close"],
    "PAIN_1200":   ["London Open", "New York Open", "Asian Session", "London Close"],
    "GAIN_1200":   ["London Open", "New York Open", "Asian Session", "London Close"],
    "SFX_VOL_75":  ["London Open", "New York Open", "Asian Session", "London Close"],
}

class SessionAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def analyse(self) -> dict:
        # Synthetics are continuously available and must not depend on
        # forex session clocks or kill-zone calculation.
        if self.symbol in SYNTHETIC_SYMBOLS:
            return self._build(True, "Synthetic — 24/7 tradeable", {}, None)

        now = None
        try:
            now = now_sast()
            kill_zone = is_kill_zone(now)
            hour      = now.hour
            weekday   = now.weekday()

            is_always_on = self.symbol in CRYPTO_SYMBOLS

            # Weekends: crypto trades 24/7; forex/indices are closed.
            if weekday >= 5 and not is_always_on:
                return self._build(False, "Weekend — market closed", kill_zone, now)

            # Dead hours block: only applies to traditional forex/indices.
            if hour == 23 and not is_always_on:
                return self._build(False, "Dead hours — no liquidity", kill_zone, now)

            preferred = SYMBOL_SESSIONS.get(self.symbol, ["London Open", "New York Open"])

            if kill_zone["active"] and kill_zone["name"] in preferred:
                reason = f"{kill_zone['name']} — optimal for {self.symbol}"
                return self._build(True, reason, kill_zone, now)

            if self.symbol in CRYPTO_SYMBOLS:
                return self._build(True, "Crypto — 24/7 tradeable", kill_zone, now)

            # Forex/indices: tradeable during active hours
            if 1 <= hour <= 22:
                return self._build(True, "Active session — not peak kill zone", kill_zone, now)

            return self._build(False, "Outside tradeable hours", kill_zone, now)

        except Exception as e:
            print(f"[SESSION] Error: {e}")
            return self._build(False, "Session check failed", {}, now)

    def _build(
        self, tradeable: bool, reason: str, kill_zone: dict,
        evaluated_at=None
    ) -> dict:
        print(f"[SESSION] {self.symbol}: tradeable={tradeable} | {reason}")
        return {
            "tradeable": tradeable,
            "reason":    reason,
            "kill_zone": kill_zone.get("active", False),
            "zone_name": kill_zone.get("name"),
            "peak":      kill_zone.get("active", False),
            "evaluated_at_sast": evaluated_at.isoformat() if evaluated_at else None,
        }
