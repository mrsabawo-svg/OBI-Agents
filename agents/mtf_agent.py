"""
OBI Agents — MTF Agent (Mid Timeframe)
Confirms structure on 1h. Must align with HTF bias.
Detects: structure break, order blocks, liquidity sweeps.
"""
import numpy as np

class MTFAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def analyse(self, market_data: dict, htf: dict) -> dict:
        df = market_data.get("1h")
        if df is None or len(df) < 20:
            return self._default()

        try:
            close  = df["Close"].values.flatten()
            high   = df["High"].values.flatten()
            low    = df["Low"].values.flatten()
            open_  = df["Open"].values.flatten()

            # Structure Break Detection
            recent_high = np.max(high[-20:-5])
            recent_low  = np.min(low[-20:-5])
            bos_bull    = close[-1] > recent_high   # Break of structure bullish
            bos_bear    = close[-1] < recent_low    # Break of structure bearish

            # Liquidity Sweep Detection
            sweep_low  = low[-1]  < np.min(low[-10:-1])  and close[-1] > np.min(low[-10:-1])
            sweep_high = high[-1] > np.max(high[-10:-1]) and close[-1] < np.max(high[-10:-1])

            # Order Block Detection (last bearish candle before bullish move / vice versa)
            ob_bull = open_[-3] > close[-3] and close[-1] > open_[-3]
            ob_bear = open_[-3] < close[-3] and close[-1] < open_[-3]

            # Alignment with HTF
            bias = htf.get("bias", "NEUTRAL")
            aligned = (
                (bias == "BULLISH" and (bos_bull or sweep_low or ob_bull)) or
                (bias == "BEARISH" and (bos_bear or sweep_high or ob_bear))
            )

            confluence = sum([bos_bull or bos_bear, sweep_low or sweep_high, ob_bull or ob_bear])

            print(f"[MTF] {self.symbol}: aligned={aligned} confluence={confluence}")
            return {
                "aligned":    aligned,
                "bos":        bos_bull or bos_bear,
                "sweep":      sweep_low or sweep_high,
                "order_block": ob_bull or ob_bear,
                "confluence": confluence,
                "direction":  "BUY" if bias == "BULLISH" else "SELL" if bias == "BEARISH" else "NEUTRAL"
            }

        except Exception as e:
            print(f"[MTF] {self.symbol} error: {e}")
            return self._default()

    def _default(self):
        return {"aligned": False, "bos": False, "sweep": False,
                "order_block": False, "confluence": 0, "direction": "NEUTRAL"}
