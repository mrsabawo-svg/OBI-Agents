"""
OBI Agents — LTF Agent (Low Timeframe)
Finds the precise entry on 5m/15m.
Looks for: OB retest, FVG fill, sweep confirmation, engulfing.
"""
import numpy as np

class LTFAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def analyse(self, market_data: dict, mtf: dict) -> dict:
        df = market_data.get("5m") or market_data.get("15m")
        if df is None or len(df) < 20:
            return self._default()

        try:
            close = df["Close"].values.flatten()
            high  = df["High"].values.flatten()
            low   = df["Low"].values.flatten()
            open_ = df["Open"].values.flatten()

            direction = mtf.get("direction", "NEUTRAL")
            if direction == "NEUTRAL":
                return self._default()

            # Engulfing candle
            bull_engulf = (close[-1] > open_[-1]) and (close[-1] > open_[-2]) and (open_[-1] < close[-2])
            bear_engulf = (close[-1] < open_[-1]) and (close[-1] < open_[-2]) and (open_[-1] > close[-2])

            # Fair Value Gap (FVG)
            fvg_bull = low[-1] > high[-3]    # gap between -3 and -1 candle
            fvg_bear = high[-1] < low[-3]

            # OB Retest (price returning to last opposing candle)
            ob_retest_bull = (open_[-2] > close[-2]) and (low[-1] <= open_[-2]) and (close[-1] > open_[-2])
            ob_retest_bear = (open_[-2] < close[-2]) and (high[-1] >= open_[-2]) and (close[-1] < open_[-2])

            # Momentum (last 3 candles in direction)
            momentum_bull = all(close[-i] > open_[-i] for i in range(1, 4))
            momentum_bear = all(close[-i] < open_[-i] for i in range(1, 4))

            if direction == "BUY":
                trigger    = bull_engulf or ob_retest_bull
                fvg        = fvg_bull
                momentum   = momentum_bull
            else:
                trigger    = bear_engulf or ob_retest_bear
                fvg        = fvg_bear
                momentum   = momentum_bear

            confluence = sum([trigger, fvg, momentum, mtf.get("sweep", False)])
            entry      = float(close[-1])
            atr_col    = [c for c in df.columns if "ATR" in c]
            atr        = float(df[atr_col[0]].iloc[-1]) if atr_col else entry * 0.001

            sl  = round(entry - atr * 1.5, 5) if direction == "BUY" else round(entry + atr * 1.5, 5)
            tp1 = round(entry + atr * 2,   5) if direction == "BUY" else round(entry - atr * 2,   5)
            tp2 = round(entry + atr * 4,   5) if direction == "BUY" else round(entry - atr * 4,   5)
            tp3 = round(entry + atr * 6,   5) if direction == "BUY" else round(entry - atr * 6,   5)
            rr  = round(abs(tp1 - entry) / (abs(entry - sl) + 1e-9), 2)

            print(f"[LTF] {self.symbol}: trigger={trigger} fvg={fvg} confluence={confluence} RR={rr}")
            return {
                "trigger":    trigger,
                "fvg":        fvg,
                "momentum":   momentum,
                "confluence": confluence,
                "entry":      entry,
                "sl":         sl,
                "tp1":        tp1,
                "tp2":        tp2,
                "tp3":        tp3,
                "rr":         rr,
                "atr":        atr,
                "direction":  direction,
                "valid":      trigger and confluence >= 2
            }

        except Exception as e:
            print(f"[LTF] {self.symbol} error: {e}")
            return self._default()

    def _default(self):
        return {"trigger": False, "fvg": False, "momentum": False,
                "confluence": 0, "entry": 0, "sl": 0, "tp1": 0,
                "tp2": 0, "tp3": 0, "rr": 0, "atr": 0,
                "direction": "NEUTRAL", "valid": False}
