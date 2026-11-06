import unittest
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import pytz

from replay.backtest_engine import BacktestEngine

SAST = pytz.timezone("Africa/Johannesburg")


def _generate_synthetic_ohlcv(n_bars=80, start_price=100.0, trend="UP"):
    timestamps = [datetime(2026, 9, 1, 9, 0) + timedelta(minutes=5 * i) for i in range(n_bars)]

    close = np.zeros(n_bars)
    open_ = np.zeros(n_bars)
    high = np.zeros(n_bars)
    low = np.zeros(n_bars)

    current = start_price
    for i in range(n_bars):
        step = 0.5 if trend == "UP" else -0.5
        open_[i] = current
        current += step
        close[i] = current
        high[i] = max(open_[i], close[i]) + 0.3
        low[i] = min(open_[i], close[i]) - 0.3

    # Force a trigger pattern on the 55th candle: bullish engulfing
    if trend == "UP" and n_bars > 55:
        open_[53] = close[53] + 0.5
        open_[54] = close[53] - 0.2
        close[54] = open_[53] + 0.5
        high[54] = close[54] + 0.4
        low[54] = open_[54] - 0.4

    df = pd.DataFrame({
        "Open": open_,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": np.full(n_bars, 1000.0),
        "EMA_20": close - 1.0,
        "EMA_50": close - 2.0,
        "ATRr_14": np.full(n_bars, 0.5),
    }, index=pd.DatetimeIndex(timestamps))

    return df


class BacktestReplayTest(unittest.TestCase):
    def test_backtest_replay_tracks_signal_and_tp_resolution(self):
        engine = BacktestEngine("BTCUSD")
        ohlcv = _generate_synthetic_ohlcv(n_bars=70, start_price=100.0, trend="UP")

        result = engine.run_replay(ohlcv_5m=ohlcv, start_idx=40)

        self.assertEqual(result["symbol"], "BTCUSD")
        self.assertEqual(result["total_bars"], 70)
        self.assertGreaterEqual(result["trades_generated"], 1)

        # Check that generated trades have canonical archive schema fields
        for trade in result["closed_trades"]:
            self.assertIn("direction", trade)
            self.assertIn("entry", trade)
            self.assertIn("status", trade)
            self.assertIn("outcome", trade)
            self.assertIn("factors", trade)
            self.assertIn("obi_score", trade)
            self.assertEqual(trade["status"], "CLOSED")
            self.assertIn(trade["outcome"], ["TP1", "TP2", "TP3", "SL", "AMBIGUOUS"])

    def test_backtest_replay_records_ambiguous_same_candle_resolution(self):
        engine = BacktestEngine("BTCUSD")

        # Test direct lifecycle evaluation with high/low breaching both sides
        candle_ambiguous = {"High": 115.0, "Low": 85.0}
        now = SAST.localize(datetime(2026, 9, 25, 10, 0))

        open_trades = [{
            "symbol": "BTCUSD", "direction": "BUY",
            "entry": 100.0, "sl": 90.0, "tp1": 110.0, "tp2": 120.0, "tp3": 130.0,
            "status": "OPEN", "factors": ["HTF"], "obi_score": 85, "grade": "A",
        }]
        closed_trades = []

        engine._evaluate_open_trades(open_trades, closed_trades, candle_ambiguous, now)

        self.assertEqual(len(open_trades), 0)
        self.assertEqual(len(closed_trades), 1)
        self.assertEqual(closed_trades[0]["outcome"], "AMBIGUOUS")
        self.assertEqual(closed_trades[0]["resolution_note"], "AMBIGUOUS_SAME_CANDLE_TARGET_AND_STOP")


if __name__ == "__main__":
    unittest.main()
