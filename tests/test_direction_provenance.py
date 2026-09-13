import numpy as np
import pandas as pd

from agents.htf_agent import HTFAgent
from agents.mtf_agent import MTFAgent
from agents.bias_agent import BiasAgent
from agents.trigger_agent import TriggerAgent


def frame(direction: str, n: int = 40) -> pd.DataFrame:
    if direction == "BULLISH":
        close = np.arange(100.0, 100.0 + n)
        ema20 = close - 1.0
        ema50 = close - 2.0
    else:
        close = np.arange(200.0, 200.0 - n, -1.0)
        ema20 = close + 1.0
        ema50 = close + 2.0

    high = close + 0.5
    low = close - 0.5
    open_ = close - 0.2 if direction == "BULLISH" else close + 0.2

    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close,
                         "EMA_20": ema20, "EMA_50": ema50})


def test_bullish_direction_propagates_htf_to_mtf_bias_trigger():
    data = {"4h": frame("BULLISH"), "1h": frame("BULLISH"), "15m": frame("BULLISH")}

    htf = HTFAgent("TEST").analyse(data)
    assert htf["bias"] == "BULLISH"
    assert htf["confidence"] >= 0.8

    mtf = MTFAgent("TEST").analyse(data, htf)
    assert mtf["aligned"] is True
    assert mtf["direction"] == "BUY"

    session = {"tradeable": True, "kill_zone": True, "reason": "controlled test"}
    regime = {"label": "TRENDING", "confidence": 0.8}
    bias = BiasAgent("TEST").evaluate(htf, mtf, session, regime)
    assert bias.approved is True
    assert bias.direction == "BUY"

    ltf = {"valid": True, "rr": 2.0, "confluence": 2, "entry": 100.0, "sl": 99.0, "tp1": 102.0, "tp2": 103.0, "tp3": 104.0}
    zone = {"zone_aligned": True, "ob_bull": True, "ob_bear": False, "in_discount": True, "in_premium": False}
    trigger = TriggerAgent("TEST").evaluate(ltf, zone, bias)
    assert trigger.fire is True
    assert trigger.direction == "BUY"


def test_bearish_direction_propagates_htf_to_mtf_bias_trigger():
    data = {"4h": frame("BEARISH"), "1h": frame("BEARISH"), "15m": frame("BEARISH")}

    htf = HTFAgent("TEST").analyse(data)
    assert htf["bias"] == "BEARISH"
    assert htf["confidence"] >= 0.8

    mtf = MTFAgent("TEST").analyse(data, htf)
    assert mtf["aligned"] is True
    assert mtf["direction"] == "SELL"

    session = {"tradeable": True, "kill_zone": True, "reason": "controlled test"}
    regime = {"label": "TRENDING", "confidence": 0.8}
    bias = BiasAgent("TEST").evaluate(htf, mtf, session, regime)
    assert bias.approved is True
    assert bias.direction == "SELL"

    ltf = {"valid": True, "rr": 2.0, "confluence": 2, "entry": 200.0, "sl": 201.0, "tp1": 198.0, "tp2": 197.0, "tp3": 196.0}
    zone = {"zone_aligned": True, "ob_bull": False, "ob_bear": True, "in_discount": False, "in_premium": True}
    trigger = TriggerAgent("TEST").evaluate(ltf, zone, bias)
    assert trigger.fire is True
    assert trigger.direction == "SELL"


def test_bias_and_trigger_do_not_invent_direction():
    htf = {"bias": "BULLISH", "confidence": 1.0}
    mtf = {"aligned": True, "direction": "NEUTRAL", "bos": True, "sweep": False, "order_block": True}
    session = {"tradeable": True, "kill_zone": True, "reason": "controlled test"}
    regime = {"label": "TRENDING", "confidence": 0.8}

    bias = BiasAgent("TEST").evaluate(htf, mtf, session, regime)
    assert bias.approved is True
    assert bias.direction == "NEUTRAL"

    ltf = {"valid": True, "rr": 2.0, "confluence": 2}
    zone = {"zone_aligned": True}
    trigger = TriggerAgent("TEST").evaluate(ltf, zone, bias)
    assert trigger.fire is False
    assert trigger.reason == "No directional bias"
