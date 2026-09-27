import numpy as np
import pandas as pd

from agents.htf_agent import HTFAgent
from agents.mtf_agent import MTFAgent


def make_frame(bullish=True, n=40):
    if bullish:
        close = np.arange(100.0, 100.0 + n)
        ema20, ema50 = close - 1, close - 2
    else:
        close = np.arange(200.0, 200.0 - n, -1.0)
        ema20, ema50 = close + 1, close + 2
    return pd.DataFrame({
        "Open": close - 0.2 if bullish else close + 0.2,
        "High": close + 0.5,
        "Low": close - 0.5,
        "Close": close,
        "EMA_20": ema20,
        "EMA_50": ema50,
    })


def test_htf_bullish_factor_provenance():
    htf = HTFAgent("NASDAQ").analyse({"4h": make_frame(True)})
    assert htf["bias"] == "BULLISH"
    assert htf["bull_score"] == 5
    assert htf["bear_score"] == 0
    assert htf["confidence"] == 1.0


def test_htf_bearish_factor_provenance():
    htf = HTFAgent("XAUUSD").analyse({"4h": make_frame(False)})
    assert htf["bias"] == "BEARISH"
    assert htf["bear_score"] == 5
    assert htf["bull_score"] == 0
    assert htf["confidence"] == 1.0


def test_mtf_records_all_five_directional_factors_bullish():
    data = {"1h": make_frame(True)}
    htf = {"bias": "BULLISH"}
    mtf = MTFAgent("NASDAQ").analyse(data, htf)
    assert mtf["direction"] == "BUY"
    assert mtf["confluence"] >= 1
    assert set(["bos", "sweep", "order_block", "confluence", "direction"]).issubset(mtf)


def test_mtf_records_all_five_directional_factors_bearish():
    data = {"1h": make_frame(False)}
    htf = {"bias": "BEARISH"}
    mtf = MTFAgent("XAUUSD").analyse(data, htf)
    assert mtf["direction"] == "SELL"
    assert mtf["confluence"] >= 1
    assert set(["bos", "sweep", "order_block", "confluence", "direction"]).issubset(mtf)
