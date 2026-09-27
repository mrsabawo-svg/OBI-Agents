import numpy as np
import pandas as pd

from replay.engine import DirectionReplay
from replay.schema import ReplayCase, REQUIRED_AGENT_INPUTS


def market_frame(direction):
    n = 40
    base = np.arange(n, dtype=float)
    if direction == "BUY":
        close = 100 + base
        high = close + 1
        low = close - 1
        open_ = close - 0.5
        ema20 = close - 2
        ema50 = close - 4
    else:
        close = 200 - base
        high = close + 1
        low = close - 1
        open_ = close + 0.5
        ema20 = close + 2
        ema50 = close + 4

    return pd.DataFrame({
        "Open": open_, "High": high, "Low": low, "Close": close,
        "EMA_20": ema20, "EMA_50": ema50,
    })


def case(direction):
    return ReplayCase(
        case_id=f"STAGE1-{direction}",
        symbol="TESTUSD",
        opened="2026-09-01T10:00:00+02:00",
        market_data={"4h": market_frame(direction), "1h": market_frame(direction)},
        agent_inputs={
            "htf": {},
            "mtf": {},
            "ltf": {
                "valid": True,
                "rr": 2.0,
                "confluence": 3,
                "entry": 100.0,
                "sl": 99.0,
                "tp1": 102.0,
                "tp2": 103.0,
                "tp3": 104.0,
                "fvg": True,
                "momentum": True,
            },
            "zone": {"zone_aligned": True, "ob_bull": direction == "BUY", "ob_bear": direction == "SELL"},
            "session": {"tradeable": True, "kill_zone": True},
            "regime": {"label": "TRENDING", "confidence": 0.9},
        },
        expected={"direction": direction},
    )


def test_replay_contract_requires_all_agent_inputs():
    payload = case("BUY")
    payload.agent_inputs.pop("regime")
    try:
        payload.validate()
    except ValueError as exc:
        assert "regime" in str(exc)
    else:
        raise AssertionError("ReplayCase accepted an incomplete agent-input contract")


def test_buy_direction_path_is_preserved():
    result = DirectionReplay().run(case("BUY"))
    assert result["direction_path"] == {
        "htf": "BULLISH",
        "mtf": "BUY",
        "bias": "BUY",
        "trigger": "BUY",
    }
    assert result["trigger"]["fire"] is True


def test_sell_direction_path_is_preserved():
    result = DirectionReplay().run(case("SELL"))
    assert result["direction_path"] == {
        "htf": "BEARISH",
        "mtf": "SELL",
        "bias": "SELL",
        "trigger": "SELL",
    }
    assert result["trigger"]["fire"] is True


def test_replay_reports_factor_provenance():
    result = DirectionReplay().run(case("BUY"))
    assert result["htf"]["bull_score"] == 5
    assert result["htf"]["bear_score"] == 0
    assert result["mtf"]["direction"] == "BUY"
    assert result["mtf"]["confluence"] >= 1
    assert "MTF aligned" in result["bias"]["factors"]


assert REQUIRED_AGENT_INPUTS == ("htf", "mtf", "ltf", "zone", "session", "regime")
