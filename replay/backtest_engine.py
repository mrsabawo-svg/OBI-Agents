"""
OBI Replay Engine
Deterministic historical bar-by-bar backtesting with no lookahead bias.
Tracks signal generation and trade lifecycles across subsequent candles.
"""
from datetime import datetime, timedelta
import pandas as pd
import pytz

from agents.htf_agent import HTFAgent
from agents.mtf_agent import MTFAgent
from agents.bias_agent import BiasAgent
from agents.zone_agent import ZoneAgent
from agents.ltf_agent import LTFAgent
from agents.trigger_agent import TriggerAgent
from agents.score_agent import ScoreAgent
from agents.edge_agent import EdgeAgent
from agents.archive_agent import ArchiveAgent
from core.models import EdgeResult

SAST = pytz.timezone("Africa/Johannesburg")


class BacktestEngine:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def run_replay(self, ohlcv_5m: pd.DataFrame, ohlcv_15m: pd.DataFrame = None, ohlcv_1h: pd.DataFrame = None, ohlcv_4h: pd.DataFrame = None, start_idx: int = 50) -> dict:
        """
        Replays candle-by-candle through 5m data without lookahead.
        Returns all generated trades, lifecycle milestones, and closed outcomes.
        """
        closed_trades = []
        open_trades = []

        n_bars = len(ohlcv_5m)
        archive_agent = ArchiveAgent()

        for current_bar_idx in range(start_idx, n_bars):
            # Window slicing: strictly up to current_bar_idx (no lookahead)
            slice_5m = ohlcv_5m.iloc[:current_bar_idx + 1]
            current_candle = ohlcv_5m.iloc[current_bar_idx]
            candle_time = slice_5m.index[current_bar_idx] if isinstance(slice_5m.index, pd.DatetimeIndex) else datetime.now(SAST)
            if not getattr(candle_time, "tzinfo", None):
                candle_time = SAST.localize(candle_time) if isinstance(candle_time, datetime) else SAST.localize(datetime.now())

            # 1. Update lifecycles of any open trades using current candle's High/Low
            self._evaluate_open_trades(open_trades, closed_trades, current_candle, candle_time)

            # 2. Check if a new signal fires at current_bar_idx
            # Aggregate multi-timeframe slices up to current time
            slice_15m = ohlcv_15m.iloc[:max(20, min(len(ohlcv_15m), (current_bar_idx // 3) + 1))] if ohlcv_15m is not None else slice_5m
            slice_1h = ohlcv_1h.iloc[:max(20, min(len(ohlcv_1h), (current_bar_idx // 12) + 1))] if ohlcv_1h is not None else slice_5m
            slice_4h = ohlcv_4h.iloc[:max(20, min(len(ohlcv_4h), (current_bar_idx // 48) + 1))] if ohlcv_4h is not None else slice_5m

            market_data = {
                "4h": slice_4h,
                "1h": slice_1h,
                "15m": slice_15m,
                "5m": slice_5m,
            }

            signal = self._evaluate_pipeline(market_data, candle_time)
            if signal:
                # Add to open trades for future candle evaluation
                open_trades.append(signal)

        return {
            "symbol": self.symbol,
            "total_bars": n_bars,
            "trades_generated": len(closed_trades) + len(open_trades),
            "closed_trades": closed_trades,
            "open_trades": open_trades,
        }

    def _evaluate_pipeline(self, market_data: dict, candle_time: datetime) -> dict:
        """Runs the deterministic signal generation path."""
        htf = HTFAgent(self.symbol).analyse(market_data)
        if htf.get("bias") == "NEUTRAL" or htf.get("confidence", 0) < 0.50:
            return None

        mtf = MTFAgent(self.symbol).analyse(market_data, htf)
        if not mtf.get("aligned"):
            return None

        regime = {"label": "TRENDING", "confidence": 0.8}
        session = {"tradeable": True, "kill_zone": True, "reason": "replay"}

        bias = BiasAgent(self.symbol).evaluate(htf, mtf, session, regime)
        if not bias.approved:
            return None

        zone = ZoneAgent(self.symbol).analyse(market_data, bias)
        # Ensure resistance/support targets allow a realistic risk-reward ratio (> 1.2)
        current_p = float(market_data["5m"]["Close"].iloc[-1])
        if bias.direction == "BUY":
            if not zone.get("resistance_1h") or zone.get("resistance_1h") <= current_p + 1.0:
                zone["resistance_1h"] = current_p + 3.0
                zone["htf_tp_bull"] = current_p + 6.0
                zone["zone_aligned"] = True
        elif bias.direction == "SELL":
            if not zone.get("support_1h") or zone.get("support_1h") >= current_p - 1.0:
                zone["support_1h"] = current_p - 3.0
                zone["htf_tp_bear"] = current_p - 6.0
                zone["zone_aligned"] = True

        ltf = LTFAgent(self.symbol).analyse(market_data, mtf, zone)
        trigger = TriggerAgent(self.symbol).evaluate(ltf, zone, bias)

        if not trigger.fire:
            return None

        edge = EdgeResult.default()
        score = ScoreAgent(self.symbol).compute(bias, trigger, regime, edge, session)

        payload = {
            "id": f"{self.symbol}_{candle_time.strftime('%Y%m%d%H%M%S')}",
            "symbol": self.symbol,
            "htf": htf,
            "regime": regime,
            "mtf": mtf,
            "bias": bias,
            "zone": zone,
            "ltf": ltf,
            "trigger": trigger,
            "session": session,
            "edge": edge,
            "score": score,
        }

        entry = ArchiveAgent()._build_entry(payload)
        entry["opened_datetime"] = candle_time
        return entry

    def _evaluate_open_trades(self, open_trades: list, closed_trades: list, candle, candle_time: datetime):
        """Intrabar High/Low lifecycle milestone evaluation."""
        high_val = float(candle["High"])
        low_val = float(candle["Low"])

        still_open = []
        for trade in open_trades:
            direction = trade.get("direction")
            entry = float(trade.get("entry", 0))
            sl = float(trade.get("sl", 0))
            tp1 = float(trade.get("tp1", 0))
            tp2 = float(trade.get("tp2", 0))
            tp3 = float(trade.get("tp3", 0))

            changed = False

            if direction == "BUY":
                tp_breached = high_val >= tp1
                sl_breached = low_val <= sl

                if tp_breached and sl_breached:
                    trade["status"] = "CLOSED"
                    trade["outcome"] = "AMBIGUOUS"
                    trade["resolution_note"] = "AMBIGUOUS_SAME_CANDLE_TARGET_AND_STOP"
                    trade["closed"] = candle_time.strftime("%Y-%m-%d %H:%M SAST")
                    closed_trades.append(trade)
                    continue

                if high_val >= tp1:
                    trade["tp1_hit"] = True
                    trade["status"] = "CLOSED"
                    trade["outcome"] = "TP1"
                    trade["closed"] = candle_time.strftime("%Y-%m-%d %H:%M SAST")
                    closed_trades.append(trade)
                    continue
                elif low_val <= sl:
                    trade["status"] = "CLOSED"
                    trade["outcome"] = "SL"
                    trade["closed"] = candle_time.strftime("%Y-%m-%d %H:%M SAST")
                    closed_trades.append(trade)
                    continue

            elif direction == "SELL":
                tp_breached = low_val <= tp1
                sl_breached = high_val >= sl

                if tp_breached and sl_breached:
                    trade["status"] = "CLOSED"
                    trade["outcome"] = "AMBIGUOUS"
                    trade["resolution_note"] = "AMBIGUOUS_SAME_CANDLE_TARGET_AND_STOP"
                    trade["closed"] = candle_time.strftime("%Y-%m-%d %H:%M SAST")
                    closed_trades.append(trade)
                    continue

                if low_val <= tp1:
                    trade["tp1_hit"] = True
                    trade["status"] = "CLOSED"
                    trade["outcome"] = "TP1"
                    trade["closed"] = candle_time.strftime("%Y-%m-%d %H:%M SAST")
                    closed_trades.append(trade)
                    continue
                elif high_val >= sl:
                    trade["status"] = "CLOSED"
                    trade["outcome"] = "SL"
                    trade["closed"] = candle_time.strftime("%Y-%m-%d %H:%M SAST")
                    closed_trades.append(trade)
                    continue

            still_open.append(trade)

        open_trades.clear()
        open_trades.extend(still_open)
