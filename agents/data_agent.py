"""
OBI Agents — Data Agent
Fetches OHLCV data across all timeframes for a symbol.
"""
import yfinance as yf
import pandas as pd
import pandas_ta as ta

SYMBOL_MAP = {
    "XAUUSD": "GC=F",
    "EURUSD": "EURUSD=X",
    "USDJPY": "USDJPY=X",
    "GBPJPY": "GBPJPY=X",
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
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
                    auto_adjust=True
                )
                if df.empty:
                    continue
                df.ta.adx(append=True)
                df.ta.ema(length=20, append=True)
                df.ta.ema(length=50, append=True)
                df.ta.atr(append=True)
                df.dropna(inplace=True)
                data[tf] = df
                print(f"[DATA] {self.symbol} {tf}: {len(df)} candles")
            except Exception as e:
                print(f"[DATA] {self.symbol} {tf} error: {e}")
        return data
