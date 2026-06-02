"""
OBI Agents — Session Agent
Determines which trading session is active (SAST).
Only fires signals during high-probability kill zones.
"""
from core.utils import now_sast, is_kill_zone

# Best sessions per symbol
SYMBOL_SESSIONS = {
    "XAUUSD": ["London Open", "New York Open"],
    "EURUSD": ["London Open", "New York Open"],
    "USDJPY": ["London Open", "New York Open", "Asian Session"],
    "GBPJPY": ["London Open", "New York Open"],
    "BTCUSD": ["London Open", "New York Open"],
    "ETHUSD": ["London Open", "New York Open"],
}

class SessionAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def analyse(self) -> dict:
        try:
            now       = now_sast()
            kill_zone = is_kill_zone()
            hour      = now.hour
            weekday   = now.weekday()   # 0=Mon, 6=Sun

            # No trading weekends
            if weekday >= 5:
                return self._build(False, "Weekend — market closed", kill_zone)

            # No trading during dead hours (SAST)
            dead_hours = list(range(22, 24)) + list(range(0, 1))
            if hour in dead_hours:
                return self._build(False, "Dead hours — no liquidity", kill_zone)

            preferred = SYMBOL_SESSIONS.get(self.symbol, ["London Open", "New York Open"])

            if kill_zone["active"] and kill_zone["name"] in preferred:
                reason = f"{kill_zone['name']} — optimal for {self.symbol}"
                return self._build(True, reason, kill_zone)

            # Outside kill zone but still tradeable session
            if 7 <= hour <= 20:
                return self._build(True, "Active session — not peak kill zone", kill_zone)

            return self._build(False, "Outside tradeable hours", kill_zone)

        except Exception as e:
            print(f"[SESSION] Error: {e}")
            return self._build(False, "Session check failed", {})

    def _build(self, tradeable: bool, reason: str, kill_zone: dict) -> dict:
        print(f"[SESSION] {self.symbol}: tradeable={tradeable} | {reason}")
        return {
            "tradeable":  tradeable,
            "reason":     reason,
            "kill_zone":  kill_zone.get("active", False),
            "zone_name":  kill_zone.get("name"),
            "peak":       kill_zone.get("active", False)
        }
