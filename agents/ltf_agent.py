"""
OBI Agents — LTF Agent
Precise entry on 5m/15m with HTF-based targets.
"""
import numpy as np

class LTFAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def analyse(self, market_data: dict, mtf: dict, zone: dict = None) -> dict:
        df = None
        if "5m" in market_data and not market_data["5m"].empty:
            df = market_data["5m"]
        elif "15m" in market_data and not market_data["15m"].empty:
            df = market_data["15m"]

        if df is None or len(df) < 20:
            return self._default()

        try:
            close = df["Close"].squeeze().values.flatten()
            high  = df["High"].squeeze().values.flatten()
            low   = df["Low"].squeeze().values.flatten()
            open_ = df["Open"].squeeze().values.flatten()

            direction = mtf.get("direction", "NEUTRAL")
            if direction == "NEUTRAL":
                return self._default()

            # Entry patterns
            bull_engulf    = bool(close[-1] > open_[-1] and close[-1] > open_[-2] and open_[-1] < close[-2])
            bear_engulf    = bool(close[-1] < open_[-1] and close[-1] < open_[-2] and open_[-1] > close[-2])
            fvg_bull       = bool(low[-1] > high[-3])
            fvg_bear       = bool(high[-1] < low[-3])
            ob_retest_bull = bool(open_[-2] > close[-2] and low[-1] <= open_[-2] and close[-1] > open_[-2])
            ob_retest_bear = bool(open_[-2] < close[-2] and high[-1] >= open_[-2] and close[-1] < open_[-2])
            momentum_bull  = bool(all(close[-i] > open_[-i] for i in range(1, 4)))
            momentum_bear  = bool(all(close[-i] < open_[-i] for i in range(1, 4)))

            if direction == "BUY":
                trigger  = bull_engulf or ob_retest_bull
                fvg      = fvg_bull
                momentum = momentum_bull
            else:
                trigger  = bear_engulf or ob_retest_bear
                fvg      = fvg_bear
                momentum = momentum_bear

            confluence = sum([trigger, fvg, momentum, mtf.get("sweep", False)])
            entry      = float(close[-1])

            # ATR for SL
            atr_col = [c for c in df.columns if "ATR" in str(c)]
            atr     = float(df[atr_col[0]].squeeze().iloc[-1]) if atr_col else entry * 0.001

            # SL — tight, below/above LTF order block
            sl = round(entry - atr * 1.2, 5) if direction == "BUY" else round(entry + atr * 1.2, 5)

            # TP — from HTF structure via zone agent
            tp1, tp2, tp3 = self._calc_htf_targets(entry, direction, atr, zone)

            rr = round(abs(tp1 - entry) / (abs(entry - sl) + 1e-9), 2)

            print("[LTF] " + self.symbol + ": trigger=" + str(trigger) + " confluence=" + str(confluence) + " RR=" + str(rr))
            return {
                "trigger":   trigger,
                "fvg":       fvg,
                "momentum":  momentum,
                "confluence": confluence,
                "entry":     round(entry, 5),
                "sl":        sl,
                "tp1":       tp1,
                "tp2":       tp2,
                "tp3":       tp3,
                "rr":        rr,
                "atr":       atr,
                "direction": direction,
                "valid":     bool(trigger and confluence >= 2)
            }

        except Exception as e:
            print("[LTF] " + self.symbol + " error: " + str(e))
            return self._default()

    def _calc_htf_targets(self, entry: float, direction: str, atr: float, zone: dict) -> tuple:
        try:
            if direction == "BUY":
                # TP1 — nearest 1h resistance
                tp1 = zone.get("resistance_1h") or round(entry + atr * 3, 5)
                # TP2 — HTF swing high or midpoint
                tp2 = zone.get("htf_tp_bull") or zone.get("htf_swing_high") or round(entry + atr * 6, 5)
                # TP3 — beyond HTF swing (extended target)
                tp3 = round(tp2 + (tp2 - entry) * 0.5, 5)

                # Sanity check — TPs must be above entry
                if tp1 <= entry: tp1 = round(entry + atr * 3, 5)
                if tp2 <= tp1:   tp2 = round(tp1 + atr * 3, 5)
                if tp3 <= tp2:   tp3 = round(tp2 + atr * 3, 5)

            else:
                # TP1 — nearest 1h support
                tp1 = zone.get("support_1h") or round(entry - atr * 3, 5)
                # TP2 — HTF swing low
                tp2 = zone.get("htf_tp_bear") or zone.get("htf_swing_low") or round(entry - atr * 6, 5)
                # TP3 — extended target
                tp3 = round(tp2 - (entry - tp2) * 0.5, 5)

                # Sanity check — TPs must be below entry
                if tp1 >= entry: tp1 = round(entry - atr * 3, 5)
                if tp2 >= tp1:   tp2 = round(tp1 - atr * 3, 5)
                if tp3 >= tp2:   tp3 = round(tp2 - atr * 3, 5)

            return round(tp1, 5), round(tp2, 5), round(tp3, 5)

        except Exception as e:
            print("[LTF] TP calc error: " + str(e))
            if direction == "BUY":
                return round(entry + atr*3, 5), round(entry + atr*6, 5), round(entry + atr*9, 5)
            else:
                return round(entry - atr*3, 5), round(entry - atr*6, 5), round(entry - atr*9, 5)

    def _default(self):
        return {"trigger": False, "fvg": False, "momentum": False,
                "confluence": 0, "entry": 0, "sl": 0, "tp1": 0,
                "tp2": 0, "tp3": 0, "rr": 0, "atr": 0,
                "direction": "NEUTRAL", "valid": False}
