"""
OBI Agents — HTF Agent (High Timeframe)
Determines macro bias: BULLISH, BEARISH, or NEUTRAL.
"""
import numpy as np

class HTFAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def analyse(self, market_data: dict) -> dict:
        df = market_data.get("4h") or market_data.get("1h")
        if df is None or len(df) < 10:
            return self._default()

        try:
            close = df["Close"].squeeze().values.flatten()
            high  = df["High"].squeeze().values.flatten()
            low   = df["Low"].squeeze().values.flatten()

            ema20_col = [c for c in df.columns if "EMA_20" in str(c)]
            ema50_col = [c for c in df.columns if "EMA_50" in str(c)]

            if ema20_col and ema50_col:
                ema20 = df[ema20_col[0]].squeeze().values.flatten()
                ema50 = df[ema50_col[0]].squeeze().values.flatten()
            else:
                ema20 = close
                ema50 = close

            price_vs_ema20 = bool(close[-1] > ema20[-1])
            price_vs_ema50 = bool(close[-1] > ema50[-1])
            ema_stack      = bool(ema20[-1] > ema50[-1])

            highs = high[-10:]
            lows  = low[-10:]
            hh = bool(highs[-1] > highs[0])
            hl = bool(lows[-1]  > lows[0])
            lh = bool(highs[-1] < highs[0])
            ll = bool(lows[-1]  < lows[0])

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
            return {"bias": bias, "confidence": confidence,
                    "bull_score": bull_score, "bear_score": bear_score}

        except Exception as e:
            print(f"[HTF] {self.symbol} error: {e}")
            return self._default()

    def _default(self):
        return {"bias": "NEUTRAL", "confidence": 0.5,
                "bull_score": 0, "bear_score": 0}
