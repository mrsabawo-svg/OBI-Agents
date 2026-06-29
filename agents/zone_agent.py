"""
OBI Agents — Zone Agent
Identifies key price zones and HTF swing targets.
"""
import numpy as np
from core.models import BiasResult

class ZoneAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def analyse(self, market_data: dict, bias: BiasResult) -> dict:
        df_htf = None
        df_mtf = None

        if "4h" in market_data and not market_data["4h"].empty:
            df_htf = market_data["4h"]
        elif "1h" in market_data and not market_data["1h"].empty:
            df_htf = market_data["1h"]

        if "1h" in market_data and not market_data["1h"].empty:
            df_mtf = market_data["1h"]
        elif "15m" in market_data and not market_data["15m"].empty:
            df_mtf = market_data["15m"]

        if df_mtf is None:
            return self._default()

        try:
            close_mtf = df_mtf["Close"].squeeze().values.flatten()
            high_mtf  = df_mtf["High"].squeeze().values.flatten()
            low_mtf   = df_mtf["Low"].squeeze().values.flatten()
            open_mtf  = df_mtf["Open"].squeeze().values.flatten()

            current_price = float(close_mtf[-1])

            swing_high  = float(np.max(high_mtf[-50:]))
            swing_low   = float(np.min(low_mtf[-50:]))
            midpoint    = (swing_high + swing_low) / 2
            in_discount = bool(current_price < midpoint)
            in_premium  = bool(current_price > midpoint)

            resistance_1h = float(np.max(high_mtf[-20:]))
            support_1h    = float(np.min(low_mtf[-20:]))

            ob_bull_price = None
            ob_bear_price = None
            for i in range(-15, -2):
                if open_mtf[i] > close_mtf[i] and close_mtf[i+1] > open_mtf[i]:
                    ob_bull_price = float(open_mtf[i])
                if open_mtf[i] < close_mtf[i] and close_mtf[i+1] < open_mtf[i]:
                    ob_bear_price = float(open_mtf[i])

            htf_swing_high = swing_high
            htf_swing_low  = swing_low
            htf_tp_bull    = None
            htf_tp_bear    = None

            if df_htf is not None and len(df_htf) >= 5:
                high_htf = df_htf["High"].squeeze().values.flatten()
                low_htf  = df_htf["Low"].squeeze().values.flatten()
                htf_swing_high = float(np.max(high_htf[-30:]))
                htf_swing_low  = float(np.min(low_htf[-30:]))

                threshold = current_price * 0.001
                htf_highs_above = [float(h) for h in high_htf if float(h) > current_price + threshold]
                htf_lows_below  = [float(l) for l in low_htf  if float(l) < current_price - threshold]

                if not htf_highs_above and "1h" in market_data:
                    high_1h = market_data["1h"]["High"].squeeze().values.flatten()
                    htf_highs_above = [float(h) for h in high_1h[-200:] if float(h) > current_price + threshold]

                if not htf_lows_below and "1h" in market_data:
                    low_1h = market_data["1h"]["Low"].squeeze().values.flatten()
                    htf_lows_below = [float(l) for l in low_1h[-200:] if float(l) < current_price - threshold]

                if htf_highs_above:
                    htf_tp_bull = sorted(htf_highs_above)[len(htf_highs_above) // 2]
                if htf_lows_below:
                    htf_tp_bear = sorted(htf_lows_below, reverse=True)[len(htf_lows_below) // 2]

            direction    = bias.direction
            zone_aligned = bool(
                (direction == "BUY"  and in_discount) or
                (direction == "SELL" and in_premium)
            )

            print("[ZONE] " + self.symbol + ": price=" + str(round(current_price, 5)) + " aligned=" + str(zone_aligned) + " htf_tp_bull=" + str(round(htf_tp_bull, 5) if htf_tp_bull else None))

            return {
                "current_price":      current_price,
                "swing_high":         swing_high,
                "swing_low":          swing_low,
                "midpoint":           round(midpoint, 5),
                "in_discount":        in_discount,
                "in_premium":         in_premium,
                "resistance_1h":      round(resistance_1h, 5),
                "support_1h":         round(support_1h, 5),
                "ob_bull":            ob_bull_price,
                "ob_bear":            ob_bear_price,
                "htf_swing_high":     round(htf_swing_high, 5),
                "htf_swing_low":      round(htf_swing_low, 5),
                "htf_tp_bull":        round(htf_tp_bull, 5) if htf_tp_bull else None,
                "htf_tp_bear":        round(htf_tp_bear, 5) if htf_tp_bear else None,
                "zone_aligned":       zone_aligned,
                "dist_to_resistance": round(abs(resistance_1h - current_price), 5),
                "dist_to_support":    round(abs(current_price - support_1h), 5),
            }

        except Exception as e:
            print("[ZONE] " + self.symbol + " error: " + str(e))
            return self._default()

    def _default(self):
        return {
            "current_price": 0, "swing_high": 0, "swing_low": 0,
            "midpoint": 0, "in_discount": False, "in_premium": False,
            "resistance_1h": 0, "support_1h": 0, "ob_bull": None,
            "ob_bear": None, "htf_swing_high": 0, "htf_swing_low": 0,
            "htf_tp_bull": None, "htf_tp_bear": None,
            "zone_aligned": False, "dist_to_resistance": 0, "dist_to_support": 0
        }
