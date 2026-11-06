import unittest
from unittest.mock import patch, MagicMock
import numpy as np
import pandas as pd

import main


def _create_candles(trend="BULLISH", n=60):
    if trend == "BULLISH":
        close = np.arange(100.0, 100.0 + n)
        ema20 = close - 1.0
        ema50 = close - 2.0
        open_ = close - 0.3
        # For the final candle, make it a strong bullish engulfing:
        # open_[-2] > close_[-2] (red candle) and close_[-1] > open_[-1] & close_[-1] > open_[-2] & open_[-1] < close_[-2]
        open_[-2] = close[-2] + 0.5
        open_[-1] = close[-2] - 0.2
        close[-1] = open_[-2] + 0.5
    else:
        close = np.arange(200.0, 200.0 - n, -1.0)
        ema20 = close + 1.0
        ema50 = close + 2.0
        open_ = close + 0.3
        open_[-2] = close[-2] - 0.5
        open_[-1] = close[-2] + 0.2
        close[-1] = open_[-2] - 0.5

    high = np.maximum(close, open_) + 0.5
    low = np.minimum(close, open_) - 0.5

    atr = np.full(n, 0.5)
    return pd.DataFrame({
        "Open": open_,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": np.full(n, 1000.0),
        "EMA_20": ema20,
        "EMA_50": ema50,
        "ATRr_14": atr,
    })


class EndToEndPipelineTest(unittest.TestCase):
    def setUp(self):
        self.memory_store = {
            "_archive": [],
            "BTCUSD": {"signals": 0, "wins": 0, "losses": 0},
        }

    def _fake_load(self):
        return self.memory_store

    def _fake_save(self, data):
        self.memory_store.clear()
        self.memory_store.update(dict(data))

    @patch("agents.execution_agent.requests.post")
    @patch("agents.execution_agent.get_balance", return_value=1000.0)
    @patch("agents.notifier_agent.NotifierAgent.send")
    @patch("agents.signal_reviewer.SignalReviewer._groq_call", return_value="VERDICT: TAKE IT\nCONFIDENCE: 9")
    @patch("agents.persistence_agent.requests.patch")
    @patch("agents.data_agent.DataAgent.fetch")
    @patch("core.memory.requests.get")
    @patch("core.memory.requests.patch")
    def test_full_pipeline_end_to_end_buy_signal(
        self, mock_gist_patch, mock_gist_get, mock_fetch, mock_persist_patch,
        mock_groq, mock_notifier, mock_balance, mock_tg_post
    ):
        import json
        import copy
        store = copy.deepcopy(self.memory_store)

        def fake_load():
            return copy.deepcopy(store)

        def fake_save(data):
            copied = copy.deepcopy(data)
            store.clear()
            store.update(copied)
            self.memory_store.clear()
            self.memory_store.update(copied)
        mock_persist_patch.return_value.status_code = 200

        # Deterministic multi-timeframe data for a clean bullish market setup
        data = {
            "4h": _create_candles("BULLISH", 60),
            "1h": _create_candles("BULLISH", 60),
            "15m": _create_candles("BULLISH", 60),
            "5m": _create_candles("BULLISH", 60),
        }
        mock_fetch.return_value = data

        news_safe = {"safe": True, "reason": "No high impact events"}

        import core.memory as memory
        orig_load = memory.load
        orig_save = memory.save
        memory.load = fake_load
        memory.save = fake_save

        try:
            # Execute end-to-end pipeline run for BTCUSD
            with patch("agents.session_agent.SessionAgent.analyse", return_value={"tradeable": True, "reason": "optimal", "kill_zone": True, "peak": True}), \
                 patch("agents.zone_agent.ZoneAgent.analyse", return_value={"resistance_1h": 162.0, "htf_tp_bull": 165.0, "zone_aligned": True, "dist_to_resistance": 3.0}), \
                 patch("agents.exa_agent.ExaAgent.get_context", return_value="Bullish trend"), \
                 patch("agents.tracker_agent.LifecycleAgent"), \
                 patch("agents.archive_agent.load_memory", side_effect=fake_load), \
                 patch("agents.archive_agent.save_memory", side_effect=fake_save), \
                 patch("agents.persistence_agent.load_memory", side_effect=fake_load), \
                 patch("agents.persistence_agent.save_memory", side_effect=fake_save), \
                 patch("agents.intelligence_agent.load_memory", side_effect=fake_load), \
                 patch("agents.signal_reviewer.load_memory", side_effect=fake_load), \
                 patch("agents.edge_agent.load_memory", side_effect=fake_load):

                result = main.run("BTCUSD", news=news_safe)
        finally:
            memory.load = orig_load
            memory.save = orig_save

        self.assertEqual(result, {"fired": True})

        # 1. Verify Canonical Archive Entry was created by ArchiveAgent
        archive = self.memory_store.get("_archive", [])
        self.assertEqual(len(archive), 1)

        trade = archive[0]
        self.assertEqual(trade["symbol"], "BTCUSD")
        self.assertEqual(trade["direction"], "BUY")
        self.assertEqual(trade["status"], "OPEN")
        self.assertEqual(trade["outcome"], "PENDING")

        # 2. Verify Schema Integrity (M1)
        self.assertIn("factors", trade)
        self.assertTrue(len(trade["factors"]) > 0)
        self.assertIn("obi_score", trade)
        self.assertIsNotNone(trade["obi_score"])
        self.assertIn("grade", trade)
        self.assertIn("trigger_grade", trade)

        # 3. Verify Persistence State (Symbol summary updated without duplicating archive)
        self.assertEqual(len(self.memory_store["_archive"]), 1)
        self.assertEqual(self.memory_store["BTCUSD"]["signals"], 1)
        self.assertIn("last_signal_data", self.memory_store["BTCUSD"])
        self.assertEqual(self.memory_store["BTCUSD"]["last_signal_data"]["direction"], "BUY")

        # 4. Verify Execution Proposal was triggered for Crypto symbol
        pending_trade = self.memory_store.get("_pending_trade")
        self.assertIsNotNone(pending_trade)
        self.assertEqual(pending_trade["symbol"], "BTCUSD")
        self.assertEqual(pending_trade["direction"], "BUY")
        self.assertGreater(pending_trade["qty"], 0.0)


if __name__ == "__main__":
    unittest.main()
