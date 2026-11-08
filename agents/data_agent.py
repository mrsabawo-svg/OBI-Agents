"""
OBI Agents — Data Agent
Fetches OHLCV data across all timeframes for a symbol.
"""
import json
import os
import numpy as np
import requests
import yfinance as yf
import pandas as pd
import ta as ta_lib

yf.set_tz_cache_location("/tmp/yfinance_cache")

SYNTHETIC_SYMBOLS = {"PAIN_999", "GAIN_999", "PAIN_1200", "GAIN_1200", "SFX_VOL_75"}

SYMBOL_MAP = {
    "XAUUSD": "GC=F", "EURUSD": "EURUSD=X", "USDJPY": "USDJPY=X",
    "GBPJPY": "GBPJPY=X", "GBPUSD": "GBPUSD=X", "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD", "SOLUSD": "SOL-USD", "NASDAQ": "NQ=F",
    "PAIN_999": "PAIN_999", "GAIN_999": "GAIN_999",
    "PAIN_1200": "PAIN_1200", "GAIN_1200": "GAIN_1200",
    "SFX_VOL_75": "SFX_VOL_75",
}

TF_MAP = {
    "1h": ("1h", "60d"),
    "15m": ("15m", "7d"),
    "5m": ("5m", "5d"),
}
TF_4H_SOURCE = ("1h", "60d")


def _resample_4h(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1h OHLCV into 4h candles."""
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.index = pd.to_datetime(df.index)
    return df.resample("4h").agg({
        "Open": "first", "High": "max", "Low": "min",
        "Close": "last", "Volume": "sum",
    }).dropna()


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
                        self.ticker, period=period, interval=interval,
                        progress=False, auto_adjust=True, threads=False,
                    )
                    if df.empty:
                        continue
                    df = self._add_indicators(df)
                    df.dropna(inplace=True)
                data[tf] = df
                print("[DATA] " + self.symbol + " " + tf + ": " + str(len(df)) + " candles")
            except Exception as exc:
                print("[DATA] " + self.symbol + " " + tf + " error: " + str(exc))
        return data

    def _fetch_4h(self) -> pd.DataFrame:
        interval, period = TF_4H_SOURCE
        df = yf.download(
            self.ticker, period=period, interval=interval,
            progress=False, auto_adjust=True, threads=False,
        )
        if df.empty:
            return df
        df = _resample_4h(df)
        df = self._add_indicators(df)
        df.dropna(inplace=True)
        return df

    def _fetch_synthetics(self, timeframes: list) -> dict:
        """Fetch broker candles; never substitute simulated data for missing evidence."""
        data = {}
        bridge_url = os.environ.get("MT5_BRIDGE_URL")
        if not bridge_url:
            print(f"[DATA] {self.symbol}: MT5_BRIDGE_URL is not configured; refusing synthetic fallback")
            return data

        required = {
            "Open", "High", "Low", "Close", "Volume",
            "bar_status", "volume_type", "quarantine_reasons",
        }
        headers = {}
        bridge_token = os.environ.get("MT5_BRIDGE_TOKEN")
        if bridge_token:
            headers["Authorization"] = f"Bearer {bridge_token}"

        for tf in timeframes:
            try:
                response = requests.get(
                    f"{bridge_url.rstrip('/')}/ohlcv",
                    params={"symbol": self.symbol, "timeframe": tf, "count": 300},
                    headers=headers,
                    timeout=10,
                )
                response.raise_for_status()
                parsed = response.json()
                if not isinstance(parsed, dict) or parsed.get("status") != "ok":
                    print(f"[DATA] {self.symbol} {tf}: bridge response did not declare success")
                    continue
                persistence = parsed.get("raw_persistence")
                if not isinstance(persistence, dict) or persistence.get("persisted") is not True:
                    print(f"[DATA] {self.symbol} {tf}: raw evidence was not persisted; withholding data")
                    continue
                if parsed.get("symbol_id") != self.symbol or parsed.get("timeframe") != tf:
                    print(f"[DATA] {self.symbol} {tf}: response identity mismatch")
                    continue
                if (
                    not parsed.get("broker_symbol")
                    or not parsed.get("server_name")
                    or not parsed.get("broker")
                    or str(parsed.get("broker")).lower() == "unknown"
                    or str(parsed.get("server_name")).lower() == "unknown"
                    or parsed.get("account_environment") not in {"demo", "live", "contest"}
                    or parsed.get("source_type") != "broker_bars"
                ):
                    print(f"[DATA] {self.symbol} {tf}: verified broker/account identity metadata missing")
                    continue

                rows = parsed.get("data")
                if not isinstance(rows, list) or not rows:
                    print(f"[DATA] {self.symbol} {tf}: bridge returned no candle rows")
                    continue
                quality_report = parsed.get("quality_report")
                if not isinstance(quality_report, dict):
                    print(f"[DATA] {self.symbol} {tf}: durable quarantine report missing")
                    continue
                observation_ids = persistence.get("observation_ids")
                quarantine_ids = persistence.get("quarantine_ids")
                if (
                    persistence.get("inserted_count") != len(rows)
                    or not isinstance(observation_ids, list)
                    or len(observation_ids) != len(rows)
                    or not isinstance(quarantine_ids, list)
                    or len(quarantine_ids) != persistence.get("quarantine_count")
                    or not persistence.get("batch_id")
                    or quality_report.get("received_count") != len(rows)
                    or persistence.get("quarantine_count") != quality_report.get("quarantine_record_count")
                ):
                    print(f"[DATA] {self.symbol} {tf}: raw persistence/quarantine counts mismatch")
                    continue

                df = pd.DataFrame(rows)
                if "timestamp" not in df.columns or not required.issubset(df.columns):
                    print(f"[DATA] {self.symbol} {tf}: invalid bridge schema")
                    continue
                if not df["quarantine_reasons"].apply(
                    lambda value: isinstance(value, list)
                    and all(isinstance(reason, str) for reason in value)
                ).all():
                    print(f"[DATA] {self.symbol} {tf}: invalid quarantine reason schema")
                    continue

                durable_quarantine = df["quarantine_reasons"].apply(bool)
                durable_quarantine_record_count = int(
                    df["quarantine_reasons"].apply(len).sum()
                )
                observed_reason_counts = {}
                for reasons in df["quarantine_reasons"]:
                    for reason in reasons:
                        observed_reason_counts[reason] = observed_reason_counts.get(reason, 0) + 1
                if (
                    int(durable_quarantine.sum()) != quality_report.get("quarantined_observation_count")
                    or durable_quarantine_record_count != quality_report.get("quarantine_record_count")
                    or observed_reason_counts != quality_report.get("reason_counts")
                ):
                    print(f"[DATA] {self.symbol} {tf}: durable quarantine detail mismatch")
                    continue

                quality = {
                    "received_count": int(len(df)),
                    "durable_quarantined_count": int(durable_quarantine.sum()),
                    "durable_quarantine_record_count": durable_quarantine_record_count,
                    "invalid_timestamp_count": 0,
                    "invalid_numeric_count": 0,
                    "invalid_ohlc_count": 0,
                    "duplicate_timestamp_count": 0,
                    "forming_excluded_count": 0,
                    "invalid_bar_status_count": 0,
                    "invalid_volume_type_count": 0,
                }
                df = df.loc[~durable_quarantine].copy()
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
                invalid_timestamp = df["timestamp"].isna()
                quality["invalid_timestamp_count"] = int(invalid_timestamp.sum())
                df = df.loc[~invalid_timestamp].copy()
                df = df.set_index("timestamp")

                numeric_columns = ["Open", "High", "Low", "Close", "Volume"]
                for column in numeric_columns:
                    df[column] = pd.to_numeric(df[column], errors="coerce")
                numeric_valid = (
                    df[numeric_columns].notna().all(axis=1)
                    & np.isfinite(df[numeric_columns]).all(axis=1)
                )
                quality["invalid_numeric_count"] = int((~numeric_valid).sum())
                df = df.loc[numeric_valid].copy()

                duplicate_mask = df.index.duplicated(keep=False)
                quality["duplicate_timestamp_count"] = int(duplicate_mask.sum())
                if duplicate_mask.any():
                    print("[DATA_QUALITY] " + json.dumps({
                        "symbol": self.symbol, "timeframe": tf,
                        **quality, "status": "rejected_duplicate_timestamps",
                    }, sort_keys=True))
                    continue

                ohlc_valid = (
                    (df[["Open", "High", "Low", "Close"]] > 0).all(axis=1)
                    & (df["Volume"] >= 0)
                    & (df["High"] >= df[["Open", "Close"]].max(axis=1))
                    & (df["Low"] <= df[["Open", "Close"]].min(axis=1))
                    & (df["High"] >= df["Low"])
                )
                quality["invalid_ohlc_count"] = int((~ohlc_valid).sum())
                df = df.loc[ohlc_valid].copy()

                statuses = df["bar_status"].astype(str).str.lower()
                status_valid = statuses.isin({"forming", "closed"})
                quality["invalid_bar_status_count"] = int((~status_valid).sum())
                df = df.loc[status_valid].copy()
                volume_types = df["volume_type"].astype(str).str.lower()
                volume_type_valid = volume_types.eq("tick_volume")
                quality["invalid_volume_type_count"] = int((~volume_type_valid).sum())
                df = df.loc[volume_type_valid].copy()
                forming = df["bar_status"].astype(str).str.lower().eq("forming")
                quality["forming_excluded_count"] = int(forming.sum())
                df = df.loc[~forming].sort_index().copy()
                if df.empty:
                    print("[DATA_QUALITY] " + json.dumps({
                        "symbol": self.symbol, "timeframe": tf,
                        **quality, "status": "no_eligible_closed_bars",
                    }, sort_keys=True))
                    continue

                # Preserve volume semantics; Volume remains for existing OBI consumers.
                df["VolumeType"] = df["volume_type"].astype(str)
                df.attrs["source_metadata"] = {
                    "broker": parsed.get("broker"),
                    "server_name": parsed.get("server_name"),
                    "account_environment": parsed.get("account_environment"),
                    "broker_symbol": parsed.get("broker_symbol"),
                    "source_type": parsed.get("source_type"),
                    "raw_batch_id": persistence.get("batch_id"),
                }
                df.attrs["quality_report"] = quality
                df = self._add_indicators(df)
                # Indicator warm-up NaNs are feature availability, not invalid raw bars.
                df.dropna(subset=["Open", "High", "Low", "Close", "Volume"], inplace=True)
                data[tf] = df
                print("[DATA_QUALITY] " + json.dumps({
                    "symbol": self.symbol, "timeframe": tf,
                    **quality, "status": "accepted",
                }, sort_keys=True))
                print(f"[DATA] {self.symbol} {tf}: {len(df)} validated closed broker candles")
            except Exception as exc:
                print(f"[DATA] MT5 Bridge error for {self.symbol} {tf}: {exc}")
        return data

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        try:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            high = df["High"].squeeze()
            low = df["Low"].squeeze()
            close = df["Close"].squeeze()
            df["ADX_14"] = ta_lib.trend.adx(high, low, close, window=14)
            df["EMA_20"] = ta_lib.trend.ema_indicator(close, window=20)
            df["EMA_50"] = ta_lib.trend.ema_indicator(close, window=50)
            df["ATRr_14"] = ta_lib.volatility.average_true_range(high, low, close, window=14)
        except Exception as exc:
            print("[DATA] Indicator error: " + str(exc))
        return df
