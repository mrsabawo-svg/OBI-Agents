"""
OBI Agents — Zone Agent
Identifies key price zones.
"""
import numpy as np

class ZoneAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def analyse(self, market_data: dict, bias: dict) -> dict:
        df = None
        if "1h" in market_data and not market_data["1h"].empty:
            df = market_data["1h"]
        elif "15m" in market_data and not market_data["15m"].empty:
            df = market_data["15m"]

        if df is None or len(df) < 10:
            return self._default()

        try:
            close = df["Close"].squeeze().values.flatten()
            high  = df["High"].squeeze().values.flatten()
            low   = df["Low"].squeeze().values.flatten()
            open_ = df["Open"].squeeze().values.flatten()

            current_price = float(close[-1])
            swing_high    = float(np.max(high[-50:]))
            swing_low     = float(np.min(low[-50:]))
            midpoint      = (swing_high + swing_low) / 2
            in_discount   = bool(current_price < midpoint)
            in_premium    = bool(current_price > midpoint)
            resistance    = float(np.max(high[-20:]))
            support       = float(np.min(low[-20:]))

            ob_bull_price = None
            ob_bear_price = None
            for i in range(-15, -2):
                if open_[i] > close[i] and close[i+1] > open_[i]:
                    ob_bull_price = float(open_[i])
                if open_[i] < close[i] and close[i+1] < open_[i]:
                    ob_bear_price = float(open_[i])

            direction    = bias.get("direction", "NEUTRAL")
            zone_aligned = bool(
                (direction == "BUY"  and in_discount) or
                (direction == "SELL" and in_premium)
            )

            print(f"[ZONE] {self.symbol}: price={current_price:.5f} aligned={zone_aligned}")
            return {
                "current_price":      current_price,
                "swing_high":         swing_high,
                "swing_low":          swing_low,
                "midpoint":           round(midpoint, 5),
                "in_discount":        in_discount,
                "in_premium":         in_premium,
                "resistance":         round(resistance, 5),
                "support":            round(support, 5),
                "ob_bull":            ob_bull_price,
                "ob_bear":            ob_bear_price,
                "dist_to_resistance": round(abs(resistance - current_price), 5),
                "dist_to_support":    round(abs(current_price - support), 5),
                "zone_aligned":       zone_aligned,
            }

        except Exception as e:
            print(f"[ZONE] {self.symbol} error: {e}")
            return self._default()

    def _default(self):
        return {"current_price": 0, "swing_high": 0, "swing_low": 0,
                "midpoint": 0, "in_discount": False, "in_premium": False,
                "resistance": 0, "support": 0, "ob_bull": None, "ob_bear": None,
                "dist_to_resistance": 0, "dist_to_support": 0, "zone_aligned": False}
