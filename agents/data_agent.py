"""
OBI Agents — Data Agent
Fetches OHLCV data across all timeframes for a symbol.
"""
import os
import requests
import numpy as np
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
        Fetches synthetic market data for Weltrade SyntX indices.
        Attempts free MT5 bridge/REST feed if MT5_BRIDGE_URL is set,
        otherwise generates deterministic continuous tick/candle series
        matching Weltrade SyntX crash/boom and volatility specifications.
        """
        data = {}
        bridge_url = os.environ.get("MT5_BRIDGE_URL")
        for tf in timeframes:
            df = None
            if bridge_url:
                try:
                    res = requests.get(
                        f"{bridge_url.rstrip('/')}/ohlcv",
                        params={"symbol": self.symbol, "timeframe": tf},
                        timeout=5
                    )
                    if res.status_code == 200:
                        parsed = res.json()
                        if parsed and "data" in parsed:
                            df = pd.DataFrame(parsed["data"])
                            df["timestamp"] = pd.to_datetime(df["timestamp"])
                            df.set_index("timestamp", inplace=True)
                except Exception as e:
                    print(f"[DATA] MT5 Bridge error for {self.symbol} {tf}: {e}")

            if df is None or df.empty:
                df = self.generate_synthetic_ohlcv(self.symbol, tf)

            if df is not None and not df.empty:
                df = self._add_indicators(df)
                df.dropna(inplace=True)
                data[tf] = df
                print(f"[DATA] {self.symbol} {tf}: {len(df)} candles")

        return data

    @staticmethod
    def generate_synthetic_ohlcv(symbol: str, timeframe: str, count: int = 350) -> pd.DataFrame:
        """
        Generates realistic synthetic OHLCV time series for Weltrade SyntX instruments.
        Specs:
        - PAIN_999: PainX 999 (Crash index: steady upward drift with sharp crash spikes averaging ~1 in 999 ticks)
        - GAIN_999: GainX 999 (Boom index: steady downward drift with sharp boom spikes averaging ~1 in 999 ticks)
        - PAIN_1200: PainX 1200 (Crash index: steady upward drift with sharp crash spikes averaging ~1 in 1200 ticks)
        - GAIN_1200: GainX 1200 (Boom index: steady downward drift with sharp boom spikes averaging ~1 in 1200 ticks)
        - SFX_VOL_75: Continuous constant-volatility index (75% annualised volatility, Gaussian random walk with momentum)
        """
        now = pd.Timestamp.now(tz="UTC").floor("min")
        tf_freq_map = {
            "5m": ("5min", pd.Timedelta(minutes=5)),
            "15m": ("15min", pd.Timedelta(minutes=15)),
            "1h": ("1h", pd.Timedelta(hours=1)),
            "4h": ("4h", pd.Timedelta(hours=4)),
        }
        freq_str, delta = tf_freq_map.get(timeframe, ("15min", pd.Timedelta(minutes=15)))
        start_time = now - delta * (count - 1)
        timestamps = pd.date_range(start=start_time, periods=count, freq=freq_str)

        # Deterministic seed based on symbol, timeframe, and current hour for repeatable consistency
        seed = abs(hash(f"{symbol}-{timeframe}-{now.strftime('%Y-%m-%d-%H')}")) % (2**32)
        rng = np.random.RandomState(seed)

        # Baseline parameters per symbol type
        if "PAIN" in symbol:
            base_price = 100000.0
            ticks_per_spike = 1200 if "1200" in symbol else 999
            # Approx 30 ticks per minute -> 5m=150 ticks, 15m=450 ticks, 1h=1800 ticks
            ticks_per_candle = {"5m": 150, "15m": 450, "1h": 1800, "4h": 7200}.get(timeframe, 450)
            spike_prob = min(ticks_per_candle / ticks_per_spike, 0.95)
            # Upward steady drift with occasional sharp downside spike
            drift = 0.0012
            spike_mag = -0.015
            vol = 0.002
            is_spike_down = True
        elif "GAIN" in symbol:
            base_price = 100000.0
            ticks_per_spike = 1200 if "1200" in symbol else 999
            ticks_per_candle = {"5m": 150, "15m": 450, "1h": 1800, "4h": 7200}.get(timeframe, 450)
            spike_prob = min(ticks_per_candle / ticks_per_spike, 0.95)
            # Downward steady drift with occasional sharp upside boom spike
            drift = -0.0012
            spike_mag = 0.015
            vol = 0.002
            is_spike_down = False
        else:  # SFX_VOL_75
            base_price = 5000.0
            spike_prob = 0.0
            spike_mag = 0.0
            drift = 0.0001
            vol = 0.007
            is_spike_down = False

        prices = [base_price]
        opens, highs, lows, closes, volumes = [], [], [], [], []

        for i in range(count):
            curr_open = prices[-1]
            has_spike = rng.rand() < spike_prob
            candle_ret = drift + rng.normal(0, vol)
            if has_spike:
                candle_ret += spike_mag

            curr_close = max(curr_open * (1.0 + candle_ret), 1.0)
            noise_high = abs(rng.normal(0, vol * 0.7)) * curr_open
            noise_low = abs(rng.normal(0, vol * 0.7)) * curr_open

            curr_high = max(curr_open, curr_close) + noise_high
            curr_low = min(curr_open, curr_close) - noise_low

            opens.append(curr_open)
            highs.append(curr_high)
            lows.append(curr_low)
            closes.append(curr_close)
            volumes.append(int(rng.uniform(500, 2500)))

            prices.append(curr_close)

        df = pd.DataFrame(
            {
                "Open": opens,
                "High": highs,
                "Low": lows,
                "Close": closes,
                "Volume": volumes,
            },
            index=timestamps,
        )
        return df

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
