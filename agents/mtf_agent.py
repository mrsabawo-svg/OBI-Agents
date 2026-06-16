"""
OBI Agents — MTF Agent (Mid Timeframe)
Confirms structure on 1h aligned with HTF bias.
"""
import numpy as np
import pandas as pd

class MTFAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def _flatten(self, df: pd.DataFrame) -> pd.DataFrame:
        """Flatten MultiIndex columns if present."""
        if isinstance(df.columns, pd.MultiIndex):
            df = df.copy()
            df.columns = df.columns.get_level_values(0)
        return df

    def analyse(self, market_data: dict, htf: dict) -> dict:
        df = None
        if "1h" in market_data and not market_data["1h"].empty:
            df = self._flatten(market_data["1h"])
        elif "15m" in market_data and not market_data["15m"].empty:
            df = self._flatten(market_data["15m"])

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

            # EMA alignment — safe column lookup after flatten
            ema_bull = False
            ema_bear = False
            try:
                if "EMA_20" in df.columns and "EMA_50" in df.columns:
                    ema20 = float(df["EMA_20"].squeeze().iloc[-1])
                    ema50 = float(df["EMA_50"].squeeze().iloc[-1])
                    ema_bull = bool(ema20 > ema50 and close[-1] > ema20)
                    ema_bear = bool(ema20 < ema50 and close[-1] < ema20)
                else:
                    print(f"[MTF] {self.symbol}: EMA columns not found — cols: {list(df.columns)[:8]}")
            except Exception as e:
                print(f"[MTF] {self.symbol}: EMA error: {e}")

            if bias == "BULLISH":
                factors   = [bos_bull, sweep_low, ob_bull, price_above, ema_bull]
                aligned   = sum(factors) >= 1
                direction = "BUY"
                sweep     = sweep_low
                print(f"[MTF] {self.symbol}: BOS={bos_bull} sweep={sweep_low} OB={ob_bull} "
                      f"price_above={price_above} ema_bull={ema_bull}")
            else:
                factors   = [bos_bear, sweep_high, ob_bear, price_below, ema_bear]
                aligned   = sum(factors) >= 1
                direction = "SELL"
                sweep     = sweep_high
                print(f"[MTF] {self.symbol}: BOS={bos_bear} sweep={sweep_high} OB={ob_bear} "
                      f"price_below={price_below} ema_bear={ema_bear}")

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
