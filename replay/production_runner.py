"""Stage 2 raw historical replay runner.

Downloads the same Yahoo Finance instruments used by DataAgent, truncates each
frame at the archived signal-open timestamp, then runs the real decision path:
HTF -> MTF -> Bias -> Zone -> LTF -> Trigger.

The replay keeps Yahoo's UTC index while constructing 4h candles so the
resampling boundary matches DataAgent's production implementation. The
historical session decision is evaluated separately in SAST.

This is an investigation tool. It never mutates the production archive and it
never treats a replay as proof of profitability.
"""
from datetime import datetime, timedelta
import json
from pathlib import Path

import pandas as pd
import pytz
import yfinance as yf

from agents.htf_agent import HTFAgent
from agents.mtf_agent import MTFAgent
from agents.bias_agent import BiasAgent
from agents.zone_agent import ZoneAgent
from agents.ltf_agent import LTFAgent
from agents.regime_agent import RegimeAgent
from agents.trigger_agent import TriggerAgent
from core.utils import SAST

SYMBOL_MAP = {"XAUUSD": "GLD", "NASDAQ": "QQQ"}
UTC = pytz.UTC


def _download(ticker: str, opened_sast: datetime, interval: str, lookback_days: int):
    opened_utc = opened_sast.astimezone(UTC)
    start = opened_utc - timedelta(days=lookback_days)
    end = opened_utc + timedelta(hours=1)
    df = yf.download(ticker, start=start, end=end, interval=interval, progress=False, auto_adjust=True, threads=False)
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize(UTC)
    return df[df.index <= opened_utc].copy()


def _with_indicators(df: pd.DataFrame) -> pd.DataFrame:
    from agents.data_agent import DataAgent
    return DataAgent("NASDAQ")._add_indicators(df.copy())


def _resample_4h(df: pd.DataFrame) -> pd.DataFrame:
    from agents.data_agent import _resample_4h
    return _resample_4h(df)


def historical_session(opened: datetime, symbol: str) -> dict:
    hour, weekday = opened.hour, opened.weekday()
    if weekday >= 5 and symbol not in {"BTCUSD", "ETHUSD", "SOLUSD"}:
        return {"tradeable": False, "reason": "Weekend — market closed", "kill_zone": False, "zone_name": None, "peak": False}
    if hour == 23 and symbol not in {"BTCUSD", "ETHUSD", "SOLUSD"}:
        return {"tradeable": False, "reason": "Dead hours — no liquidity", "kill_zone": False, "zone_name": None, "peak": False}
    zones = {"London Open": (9, 11), "New York Open": (15, 17), "Asian Session": (1, 3)}
    zone_name = next((name for name, (start, end) in zones.items() if start <= hour < end), None)
    peak = zone_name is not None
    preferred = {"XAUUSD": ["London Open", "New York Open"], "NASDAQ": ["New York Open"]}.get(symbol, [])
    reason = f"{zone_name} — optimal for {symbol}" if peak and zone_name in preferred else "Active session — not peak kill zone"
    return {"tradeable": 1 <= hour <= 22, "reason": reason, "kill_zone": peak, "zone_name": zone_name, "peak": peak}


def run_case(target: dict) -> dict:
    symbol = target["symbol"]
    opened = SAST.localize(datetime.strptime(target["opened"], "%Y-%m-%d %H:%M SAST"))
    ticker = SYMBOL_MAP[symbol]

    one_h = _download(ticker, opened, "1h", 60)
    fifteen = _download(ticker, opened, "15m", 7)
    five = _download(ticker, opened, "5m", 5)
    four_h = _resample_4h(one_h) if not one_h.empty else pd.DataFrame()
    market_data = {"1h": one_h, "15m": fifteen, "5m": five, "4h": four_h}
    market_data = {k: _with_indicators(v) if not v.empty else v for k, v in market_data.items()}

    htf = HTFAgent(symbol).analyse(market_data)
    mtf = MTFAgent(symbol).analyse(market_data, htf)
    session = historical_session(opened, symbol)
    regime = RegimeAgent(symbol).detect(market_data)
    bias = BiasAgent(symbol).evaluate(htf, mtf, session, regime)
    zone = ZoneAgent(symbol).analyse(market_data, bias)
    ltf = LTFAgent(symbol).analyse(market_data, mtf, zone)
    trigger = TriggerAgent(symbol).evaluate(ltf, zone, bias)

    return {
        "signal_id": target["signal_id"],
        "symbol": symbol,
        "opened": target["opened"],
        "archive": {"direction": target["direction"], "outcome": target["outcome"], "entry": target["entry"], "sl": target["sl"], "tp1": target["tp1"]},
        "replay": {
            "direction_path": {"htf": htf.get("bias", "NEUTRAL"), "mtf": mtf.get("direction", "NEUTRAL"), "bias": bias.direction, "trigger": trigger.direction},
            "htf": htf, "mtf": mtf, "regime": regime, "session": session,
            "bias": {"approved": bias.approved, "direction": bias.direction, "grade": bias.grade, "score": bias.score, "factors": bias.factors, "reason": bias.reason},
            "zone": zone, "ltf": ltf,
            "trigger": {"fire": trigger.fire, "direction": trigger.direction, "grade": trigger.grade, "entry": trigger.entry, "sl": trigger.sl, "tp1": trigger.tp1, "tp2": trigger.tp2, "tp3": trigger.tp3, "rr": trigger.rr, "reason": trigger.reason},
        },
        "comparison": {"direction_match": trigger.direction == target["direction"], "archive_outcome": target["outcome"], "outcome_verified": False},
        "data_counts": {k: len(v) for k, v in market_data.items()},
    }


def main():
    targets = json.loads(Path("replay/cases/production_archive_targets.json").read_text())["targets"]
    results = [run_case(target) for target in targets]
    for result in results:
        print(json.dumps({"signal_id": result["signal_id"], "archive_direction": result["archive"]["direction"], "replay_direction_path": result["replay"]["direction_path"], "direction_match": result["comparison"]["direction_match"], "data_counts": result["data_counts"]}, sort_keys=True))
    Path("replay_stage2_results.json").write_text(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
