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
from core.feature_lineage import build_indicator_lineage

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
            "bar_status", "volume_type", "quarantine_reasons", "observation_id",
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
                account_identity = parsed.get("account_identity")
                fingerprint = (
                    account_identity.get("account_fingerprint")
                    if isinstance(account_identity, dict) else None
                )
                fingerprint_valid = (
                    isinstance(fingerprint, str)
                    and len(fingerprint) == 64
                    and all(char in "0123456789abcdef" for char in fingerprint)
                )
                if (
                    not parsed.get("broker_symbol")
                    or not parsed.get("server_name")
                    or not parsed.get("broker")
                    or str(parsed.get("broker")).lower() == "unknown"
                    or str(parsed.get("server_name")).lower() == "unknown"
                    or parsed.get("account_environment") not in {"demo", "live", "contest"}
                    or parsed.get("source_type") != "broker_bars"
                    or not fingerprint_valid
                    or not account_identity.get("broker_company")
                    or account_identity.get("server") != parsed.get("server_name")
                    or account_identity.get("environment") != parsed.get("account_environment")
                    or account_identity.get("platform") != parsed.get("platform")
                ):
                    print(f"[DATA] {self.symbol} {tf}: verified broker/account identity metadata missing or inconsistent")
                    continue

                rows = parsed.get("data")
                if not isinstance(rows, list) or not rows:
                    print(f"[DATA] {self.symbol} {tf}: bridge returned no candle rows")
                    continue
                quality_report = parsed.get("quality_report")
                if (
                    not isinstance(quality_report, dict)
                    or not quality_report.get("report_id")
                    or quality_report.get("account_fingerprint") != fingerprint
                ):
                    print(f"[DATA] {self.symbol} {tf}: durable validation report or account fingerprint missing")
                    continue
                history_report = quality_report.get("history_completeness")
                pagination = (
                    history_report.get("pagination")
                    if isinstance(history_report, dict) else None
                )
                page_sizes = pagination.get("page_sizes") if isinstance(pagination, dict) else None
                page_starts = (
                    pagination.get("page_start_positions")
                    if isinstance(pagination, dict) else None
                )
                page_count = pagination.get("page_count") if isinstance(pagination, dict) else None
                forming_count = history_report.get("forming_count") if isinstance(history_report, dict) else None
                closed_count = history_report.get("closed_count") if isinstance(history_report, dict) else None
                if isinstance(forming_count, bool) or not isinstance(forming_count, int):
                    forming_count = -1
                if isinstance(closed_count, bool) or not isinstance(closed_count, int):
                    closed_count = -1
                forming_indexes = (
                    history_report.get("forming_observation_indexes")
                    if isinstance(history_report, dict) else None
                )
                requested_start_pos = (
                    history_report.get("requested_start_pos")
                    if isinstance(history_report, dict) else None
                )
                expected_page_starts = []
                if (
                    isinstance(requested_start_pos, int)
                    and not isinstance(requested_start_pos, bool)
                    and isinstance(page_sizes, list)
                    and all(isinstance(value, int) and not isinstance(value, bool) for value in page_sizes)
                ):
                    cursor = requested_start_pos
                    for page_size in page_sizes:
                        expected_page_starts.append(cursor)
                        cursor += page_size
                expected_forming_indexes = [
                    index for index, row in enumerate(rows)
                    if isinstance(row, dict) and row.get("bar_status") == "forming"
                ]
                pagination_valid = (
                    isinstance(page_sizes, list)
                    and isinstance(page_starts, list)
                    and isinstance(page_count, int) and not isinstance(page_count, bool)
                    and page_count == len(page_sizes) == len(page_starts)
                    and all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in page_sizes)
                    and all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in page_starts)
                    and sum(page_sizes) == len(rows)
                    and page_starts == expected_page_starts
                )
                forming_valid = (
                    isinstance(forming_indexes, list)
                    and all(isinstance(value, int) and not isinstance(value, bool) for value in forming_indexes)
                    and sorted(forming_indexes) == expected_forming_indexes
                )
                if (
                    not isinstance(history_report, dict)
                    or history_report.get("schema_version") != "obi-history-completeness-v1"
                    or history_report.get("status") != "complete"
                    or history_report.get("coverage_scope") != "requested_window"
                    or history_report.get("requested_start_pos") != parsed.get("start_pos", 0)
                    or history_report.get("timeframe") != tf
                    or history_report.get("instrument_id") != self.symbol
                    or history_report.get("broker_symbol") != parsed.get("broker_symbol")
                    or history_report.get("account_fingerprint") != fingerprint
                    or history_report.get("requested_count") != history_report.get("returned_count")
                    or history_report.get("returned_count") != len(rows)
                    or closed_count != len(rows) - forming_count
                    or closed_count < 1
                    or forming_count != len(expected_forming_indexes)
                    or not forming_valid
                    or not pagination_valid
                    or history_report.get("snapshot_stable") is not True
                    or (
                        page_count > 1
                        and history_report.get("snapshot_check_performed") is not True
                    )
                    or history_report.get("valid_timestamp_count") != len(rows)
                    or history_report.get("invalid_timestamp_count") != 0
                    or history_report.get("missing_interval_count") != 0
                    or history_report.get("gap_segment_count") != 0
                    or history_report.get("duplicate_timestamp_count") != 0
                    or history_report.get("duplicate_observation_count") != 0
                    or history_report.get("duplicate_group_count") != 0
                    or history_report.get("gaps") != []
                    or history_report.get("out_of_order_count") != 0
                    or history_report.get("timeframe_alignment_error_count") != 0
                    or history_report.get("future_bar_count") != 0
                    or history_report.get("latest_bar_stale") is not False
                    or history_report.get("freshness_status") != "fresh"
                    or history_report.get("reasons") != []
                ):
                    status = history_report.get("status") if isinstance(history_report, dict) else "missing"
                    print(f"[DATA] {self.symbol} {tf}: history completeness is {status}; withholding signal data")
                    continue
                canonical_persistence = parsed.get("canonical_persistence")
                observation_ids = persistence.get("observation_ids")
                quarantine_ids = persistence.get("quarantine_ids")
                quarantine_links = persistence.get("quarantine_records")
                if (
                    persistence.get("account_fingerprint") != fingerprint
                    or persistence.get("inserted_count") != len(rows)
                    or not isinstance(observation_ids, list)
                    or len(observation_ids) != len(rows)
                    or any(not isinstance(item, str) or not item for item in observation_ids)
                    or len(set(observation_ids)) != len(observation_ids)
                    or not isinstance(quarantine_ids, list)
                    or len(quarantine_ids) != persistence.get("quarantine_count")
                    or len(set(quarantine_ids)) != len(quarantine_ids)
                    or not isinstance(quarantine_links, list)
                    or len(quarantine_links) != len(quarantine_ids)
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
                if df["observation_id"].tolist() != observation_ids:
                    print(f"[DATA] {self.symbol} {tf}: row-to-raw-observation identity mismatch")
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
                expected_reasons_by_observation = {}
                link_ids = set()
                valid_links = True
                for link in quarantine_links:
                    if (
                        not isinstance(link, dict)
                        or link.get("quarantine_id") not in quarantine_ids
                        or link.get("observation_id") not in observation_ids
                        or not isinstance(link.get("reason_code"), str)
                    ):
                        valid_links = False
                        break
                    link_ids.add(link["quarantine_id"])
                    observation_id = link["observation_id"]
                    expected_reasons_by_observation.setdefault(observation_id, []).append(link["reason_code"])
                    reason = link["reason_code"]
                    observed_reason_counts[reason] = observed_reason_counts.get(reason, 0) + 1
                annotations_match_links = all(
                    row["quarantine_reasons"] == expected_reasons_by_observation.get(row["observation_id"], [])
                    for _, row in df.iterrows()
                )
                if (
                    not valid_links
                    or link_ids != set(quarantine_ids)
                    or not annotations_match_links
                    or int(durable_quarantine.sum()) != quality_report.get("quarantined_observation_count")
                    or durable_quarantine_record_count != quality_report.get("quarantine_record_count")
                    or observed_reason_counts != quality_report.get("reason_counts")
                ):
                    print(f"[DATA] {self.symbol} {tf}: durable quarantine detail mismatch")
                    continue

                if not isinstance(canonical_persistence, dict):
                    print(f"[DATA] {self.symbol} {tf}: canonical persistence receipt missing")
                    continue
                canonical_lineage = canonical_persistence.get("source_lineage")
                canonical_ids = canonical_persistence.get("candle_ids")
                expected_canonical_observations = df.loc[
                    ~durable_quarantine, "observation_id"
                ].tolist()
                if (
                    canonical_persistence.get("persisted") is not True
                    or canonical_persistence.get("validation_report_id") != quality_report.get("report_id")
                    or canonical_persistence.get("inserted_count") != len(expected_canonical_observations)
                    or not isinstance(canonical_lineage, list)
                    or len(canonical_lineage) != len(expected_canonical_observations)
                    or not isinstance(canonical_ids, list)
                    or len(canonical_ids) != len(expected_canonical_observations)
                    or any(not isinstance(item, str) or not item for item in canonical_ids)
                    or len(set(canonical_ids)) != len(canonical_ids)
                    or not isinstance(canonical_persistence.get("validation_report_sha256"), str)
                    or len(canonical_persistence.get("validation_report_sha256")) != 64
                    or any(
                        char not in "0123456789abcdef"
                        for char in canonical_persistence.get("validation_report_sha256", "").lower()
                    )
                ):
                    print(f"[DATA] {self.symbol} {tf}: canonical persistence counts/report mismatch")
                    continue
                canonical_by_observation = {}
                valid_canonical_lineage = True
                for link in canonical_lineage:
                    if (
                        not isinstance(link, dict)
                        or not isinstance(link.get("observation_id"), str)
                        or not isinstance(link.get("candle_id"), str)
                        or not link["observation_id"]
                        or not link["candle_id"]
                        or link["observation_id"] in canonical_by_observation
                    ):
                        valid_canonical_lineage = False
                        break
                    canonical_by_observation[link["observation_id"]] = link["candle_id"]
                if (
                    not valid_canonical_lineage
                    or list(canonical_by_observation.keys()) != expected_canonical_observations
                    or list(canonical_by_observation.values()) != canonical_ids
                ):
                    print(f"[DATA] {self.symbol} {tf}: canonical-to-raw lineage mismatch")
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
                if len(df) != len(canonical_by_observation):
                    print(f"[DATA] {self.symbol} {tf}: local validation disagrees with persisted canonical rows")
                    continue
                df["RawObservationID"] = df["observation_id"]
                df["CanonicalCandleID"] = df["observation_id"].map(canonical_by_observation)
                if df["CanonicalCandleID"].isna().any():
                    print(f"[DATA] {self.symbol} {tf}: canonical candle ID missing for an accepted row")
                    continue
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
                df.drop(
                    columns=["bar_status", "volume_type", "quarantine_reasons", "observation_id"],
                    inplace=True,
                    errors="ignore",
                )
                df.attrs["source_metadata"] = {
                    "broker": parsed.get("broker"),
                    "server_name": parsed.get("server_name"),
                    "account_environment": parsed.get("account_environment"),
                    "account_fingerprint": fingerprint,
                    "history_status": history_report.get("status"),
                    "history_report_id": quality_report.get("report_id"),
                    "history_requested_count": history_report.get("requested_count"),
                    "history_returned_count": history_report.get("returned_count"),
                    "history_gap_count": history_report.get("missing_interval_count"),
                    "history_forming_count": history_report.get("forming_count"),
                    "history_closed_count": history_report.get("closed_count"),
                    "broker_symbol": parsed.get("broker_symbol"),
                    "source_type": parsed.get("source_type"),
                    "raw_batch_id": persistence.get("batch_id"),
                    "validation_report_id": quality_report.get("report_id"),
                    "canonical_validation_report_sha256": canonical_persistence.get("validation_report_sha256"),
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
        df.attrs["feature_lineage"] = build_indicator_lineage(df)
        return df
