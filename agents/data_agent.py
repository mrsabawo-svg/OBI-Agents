"""
OBI Agents — Data Agent
Fetches OHLCV data across all timeframes for a symbol.
"""
import os
import requests
import yfinance as yf
import pandas as pd
import ta as ta_lib

yf.set_tz_cache_location("/tmp/yfinance_cache")

SYNTHETIC_SYMBOLS = {"PAIN_999", "GAIN_999", "PAIN_1200", "GAIN_1200", "SFX_VOL_75"}

SYMBOL_MAP = {
    "XAUUSD":     "GC=F",
    "EURUSD":     "EURUSD=X",
    "USDJPY":     "USDJPY=X",
    "GBPJPY":     "GBPJPY=X",
    "GBPUSD":     "GBPUSD=X",
    "BTCUSD":     "BTC-USD",
    "ETHUSD":     "ETH-USD",
    "SOLUSD":     "SOL-USD",
    "NASDAQ":     "NQ=F",
    "PAIN_999":   "PAIN_999",
    "GAIN_999":   "GAIN_999",
    "PAIN_1200":  "PAIN_1200",
    "GAIN_1200":  "GAIN_1200",
    "SFX_VOL_75": "SFX_VOL_75",
}

# yfinance hard limits:
#   1h  → max 730d
#   4h  → NOT a native interval — approximated via 1h then resampled
#   15m → max 60d
#   5m  → max 60d
TF_MAP = {
    "1h":  ("1h",  "60d"),   # was 60d — kept, 60d gives ~1000 candles
    "15m": ("15m", "7d"),
    "5m":  ("5m",  "5d"),
}

# 4h is resampled from 1h so we get true 4h OHLCV with full history
TF_4H_SOURCE = ("1h", "60d")   # fetch 60 days of 1h, resample → ~360 4h candles


def _resample_4h(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1h OHLCV into 4h candles."""
    df = df.copy()
    # Flatten MultiIndex columns if present (yfinance sometimes returns them)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.index = pd.to_datetime(df.index)
    resampled = df.resample("4h").agg({
        "Open":   "first",
        "High":   "max",
        "Low":    "min",
        "Close":  "last",
        "Volume": "sum",
    }).dropna()
    return resampled


class DataAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.ticker = SYMBOL_MAP.get(symbol, symbol)

    def fetch(self, timeframes: list) -> dict:
        if self.symbol in SYNTHETIC_SYMBOLS:
            return self._fetch_synthetics(timeframes)

        data = {}
        for tf in timeframes:
            try:
                if tf == "4h":
                    df = self._fetch_4h()
                else:
                    interval, period = TF_MAP.get(tf, ("15m", "7d"))
                    df = yf.download(
                        self.ticker,
                        period=period,
                        interval=interval,
                        progress=False,
                        auto_adjust=True,
                        threads=False,
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

    def _fetch_4h(self) -> pd.DataFrame:
        interval, period = TF_4H_SOURCE
        df = yf.download(
            self.ticker,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=True,
            threads=False,
        )
        if df.empty:
            return df
        df = _resample_4h(df)
        df = self._add_indicators(df)
        df.dropna(inplace=True)
        return df

    def _fetch_synthetics(self, timeframes: list) -> dict:
        """
        Fetch live Weltrade SyntX OHLCV through the configured MT5 bridge.

        No simulated/random fallback is permitted in the production analysis
        path: fabricated candles must never be mistaken for market evidence.
        """
        data = {}
        bridge_url = os.environ.get("MT5_BRIDGE_URL")
        if not bridge_url:
            print(
                "[DATA] " + self.symbol
                + ": MT5_BRIDGE_URL is not configured; refusing synthetic fallback"
            )
            return data

        required = {"Open", "High", "Low", "Close", "Volume"}
        for tf in timeframes:
            try:
                res = requests.get(
                    f"{bridge_url.rstrip('/')}/ohlcv",
                    params={"symbol": self.symbol, "timeframe": tf},
                    timeout=5,
                )
                res.raise_for_status()
                parsed = res.json()
                rows = parsed.get("data") if isinstance(parsed, dict) else None
                if not isinstance(rows, list) or not rows:
                    print(f"[DATA] {self.symbol} {tf}: bridge returned no candle rows")
                    continue

                df = pd.DataFrame(rows)
                if "timestamp" not in df.columns or not required.issubset(df.columns):
                    print(f"[DATA] {self.symbol} {tf}: invalid bridge schema")
                    continue

                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
                df = df.dropna(subset=["timestamp"]).set_index("timestamp")
                for column in required:
                    df[column] = pd.to_numeric(df[column], errors="coerce")
                df = df.dropna(subset=list(required))
                df = df[~df.index.duplicated(keep="last")].sort_index()

                if df.empty:
                    print(f"[DATA] {self.symbol} {tf}: no valid candles after parsing")
                    continue

                valid_ohlc = (
                    (df["Low"] > 0)
                    & (df["High"] >= df[["Open", "Close"]].max(axis=1))
                    & (df["Low"] <= df[["Open", "Close"]].min(axis=1))
                    & (df["High"] >= df["Low"])
                )
                df = df.loc[valid_ohlc]
                if df.empty:
                    print(f"[DATA] {self.symbol} {tf}: OHLC validation rejected all candles")
                    continue

                df = self._add_indicators(df)
                df.dropna(inplace=True)
                if not df.empty:
                    data[tf] = df
                    print(f"[DATA] {self.symbol} {tf}: {len(df)} live bridge candles")
            except Exception as e:
                print(f"[DATA] MT5 Bridge error for {self.symbol} {tf}: {e}")

        return data

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        try:
            # Flatten MultiIndex columns if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
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
