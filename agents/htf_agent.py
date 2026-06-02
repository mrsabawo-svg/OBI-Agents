"""
OBI Agents — HTF Agent (High Timeframe)
Determines the macro bias: BULLISH, BEARISH, or NEUTRAL.
Runs on 4h data. This is the top-down anchor for all signals.
"""
import numpy as np

class HTFAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def analyse(self, market_data: dict) -> dict:
        df = market_data.get("4h") or market_data.get("1h")
        if df is None or len(df) < 20:
            return self._default()

        try:
            close  = df["Close"].values.flatten()
            ema20  = df[[c for c in df.columns if "EMA_20" in c][0]].values.flatten()
            ema50  = df[[c for c in df.columns if "EMA_50" in c][0]].values.flatten()

            price_vs_ema20 = close[-1] > ema20[-1]
            price_vs_ema50 = close[-1] > ema50[-1]
            ema_stack      = ema20[-1] > ema50[-1]

            # Higher highs / higher lows check (last 10 candles)
            highs = df["High"].values.flatten()[-10:]
            lows  = df["Low"].values.flatten()[-10:]
            hh = highs[-1] > highs[-5]
            hl = lows[-1]  > lows[-5]
            lh = highs[-1] < highs[-5]
            ll = lows[-1]  < lows[-5]

            bull_score = sum([price_vs_ema20, price_vs_ema50, ema_stack, hh, hl])
            bear_score = sum([not price_vs_ema20, not price_vs_ema50, not ema_stack, lh, ll])

            if bull_score >= 4:
                bias = "BULLISH"
            elif bear_score >= 4:
                bias = "BEARISH"
            else:
                bias = "NEUTRAL"

            confidence = max(bull_score, bear_score) / 5

            print(f"[HTF] {self.symbol}: {bias} (conf={confidence:.2f})")
            return {"bias": bias, "confidence": confidence, "bull_score": bull_score, "bear_score": bear_score}

        except Exception as e:
            print(f"[HTF] {self.symbol} error: {e}")
            return self._default()

    def _default(self):
        return {"bias": "NEUTRAL", "confidence": 0.5, "bull_score": 0, "bear_score": 0}
