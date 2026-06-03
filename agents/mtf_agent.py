"""
OBI Agents — MTF Agent (Mid Timeframe)
Confirms structure on 1h aligned with HTF bias.
"""
import numpy as np

class MTFAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def analyse(self, market_data: dict, htf: dict) -> dict:
        df = None
        if "1h" in market_data and not market_data["1h"].empty:
            df = market_data["1h"]
        elif "15m" in market_data and not market_data["15m"].empty:
            df = market_data["15m"]

        if df is None or len(df) < 20:
            return self._default()

        try:
            close = df["Close"].squeeze().values.flatten()
            high  = df["High"].squeeze().values.flatten()
            low   = df["Low"].squeeze().values.flatten()
            open_ = df["Open"].squeeze().values.flatten()

            bias = htf.get("bias", "NEUTRAL")
            if bias == "NEUTRAL":
                return self._default()

            # Structure: price above/below recent range
            recent_high = np.max(high[-20:])
            recent_low  = np.min(low[-20:])
            mid         = (recent_high + recent_low) / 2
            price_above = bool(close[-1] > mid)
            price_below = bool(close[-1] < mid)

            # BOS — price broke recent swing
            swing_high_5 = np.max(high[-10:-3])
            swing_low_5  = np.min(low[-10:-3])
            bos_bull = bool(close[-1] > swing_high_5)
            bos_bear = bool(close[-1] < swing_low_5)

            # Liquidity sweep
            sweep_low  = bool(low[-1]  < np.min(low[-10:-1])  and close[-1] > np.min(low[-10:-1]))
            sweep_high = bool(high[-1] > np.max(high[-10:-1]) and close[-1] < np.max(high[-10:-1]))

            # Order block
            ob_bull = bool(open_[-3] > close[-3] and close[-1] > open_[-3])
            ob_bear = bool(open_[-3] < close[-3] and close[-1] < open_[-3])

            # EMA alignment
            ema20_col = [c for c in df.columns if "EMA_20" in str(c)]
            ema50_col = [c for c in df.columns if "EMA_50" in str(c)]
            ema_bull = False
            ema_bear = False
            if ema20_col and ema50_col:
                ema20 = float(df[ema20_col[0]].squeeze().iloc[-1])
                ema50 = float(df[ema50_col[0]].squeeze().iloc[-1])
                ema_bull = bool(ema20 > ema50 and close[-1] > ema20)
                ema_bear = bool(ema20 < ema50 and close[-1] < ema20)

            if bias == "BULLISH":
                factors    = [bos_bull, sweep_low, ob_bull, price_above, ema_bull]
                aligned    = sum(factors) >= 1
                direction  = "BUY"
                sweep      = sweep_low
            else:
                factors    = [bos_bear, sweep_high, ob_bear, price_below, ema_bear]
                aligned    = sum(factors) >= 1
                direction  = "SELL"
                sweep      = sweep_high

            confluence = sum(factors)
            print(f"[MTF] {self.symbol}: aligned={aligned} confluence={confluence} direction={direction}")
            return {
                "aligned":     aligned,
                "bos":         bos_bull or bos_bear,
                "sweep":       sweep,
                "order_block": ob_bull or ob_bear,
                "confluence":  confluence,
                "direction":   direction if aligned else "NEUTRAL"
            }

        except Exception as e:
            print(f"[MTF] {self.symbol} error: {e}")
            return self._default()

    def _default(self):
        return {"aligned": False, "bos": False, "sweep": False,
                "order_block": False, "confluence": 0, "direction": "NEUTRAL"}
