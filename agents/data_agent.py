"""
OBI Agents — Data Agent
Fetches OHLCV data across all timeframes for a symbol.
"""
import yfinance as yf
import pandas as pd
import ta as ta_lib

yf.set_tz_cache_location("/tmp/yfinance_cache")

SYMBOL_MAP = {
    "XAUUSD": "GLD",
    "EURUSD": "EURUSD=X",
    "USDJPY": "USDJPY=X",
    "GBPJPY": "GBPJPY=X",
    "GBPUSD": "GBPUSD=X",
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "SOLUSD": "SOL-USD",
    "NASDAQ": "QQQ",
}

TF_MAP = {
    "1h":  ("60m", "60d"),
    "4h":  ("1d",  "60d"),
    "15m": ("15m", "7d"),
    "5m":  ("5m",  "5d"),
}

class DataAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.ticker = SYMBOL_MAP.get(symbol, symbol)

    def fetch(self, timeframes: list) -> dict:
        data = {}
        for tf in timeframes:
            try:
                interval, period = TF_MAP.get(tf, ("15m", "7d"))
                df = yf.download(
                    self.ticker,
                    period=period,
                    interval=interval,
                    progress=False,
                    auto_adjust=True,
                    threads=False
                )
                if df.empty:
                    continue
                df = self._add_indicators(df)
                df.dropna(inplace=True)
                data[tf] = df
                print("[DATA] " + self.symbol + " " + tf + ": " + str(len(df)) + " candles")
            except Exception as e:
                print("[DATA] " + self.symbol + " " + tf + " error: " + str(e))
        return data

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        try:
            high  = df["High"].squeeze()
            low   = df["Low"].squeeze()
            close = df["Close"].squeeze()
            df["ADX_14"]  = ta_lib.trend.adx(high, low, close, window=14)
            df["EMA_20"]  = ta_lib.trend.ema_indicator(close, window=20)
            df["EMA_50"]  = ta_lib.trend.ema_indicator(close, window=50)
            df["ATRr_14"] = ta_lib.volatility.average_true_range(high, low, close, window=14)
        except Exception as e:
            print("[DATA] Indicator error: " + str(e))
        return df
