"""
Tests for Weltrade SyntX Synthetics integration across SessionAgent, ChiefAgent, DataAgent, and LifecycleAgent.
"""
import pytest
from datetime import datetime
import pytz
from unittest.mock import patch, MagicMock
import pandas as pd

from agents.session_agent import SessionAgent, SYNTHETIC_SYMBOLS
from agents.chief_agent import ChiefAgent, SYNTHETICS, SYMBOLS
from agents.data_agent import DataAgent
from agents.lifecycle_agent import LifecycleAgent
from core.models import SymbolContext

SAST = pytz.timezone("Africa/Johannesburg")

EXPECTED_SYNTHETICS = {"PAIN_999", "GAIN_999", "PAIN_1200", "GAIN_1200", "SFX_VOL_75"}


def test_synthetic_symbols_registered():
    assert EXPECTED_SYNTHETICS == SYNTHETIC_SYMBOLS
    assert EXPECTED_SYNTHETICS == SYNTHETICS
    for sym in EXPECTED_SYNTHETICS:
        assert sym in SYMBOLS


def test_synthetic_session_rules():
    # Test weekend (e.g. Saturday)
    saturday = SAST.localize(datetime(2025, 3, 1, 14, 0))  # Saturday
    with patch("agents.session_agent.now_sast", return_value=saturday):
        for sym in EXPECTED_SYNTHETICS:
            sess = SessionAgent(sym).analyse()
            assert sess["tradeable"] is True
            assert "Synthetic" in sess["reason"]

        # Ensure traditional forex is blocked on Saturday
        sess_fx = SessionAgent("EURUSD").analyse()
        assert sess_fx["tradeable"] is False
        assert "Weekend" in sess_fx["reason"]

    # Test dead hour 23:00 SAST
    dead_hour = SAST.localize(datetime(2025, 2, 28, 23, 30))  # Friday 23:30
    with patch("agents.session_agent.now_sast", return_value=dead_hour):
        for sym in EXPECTED_SYNTHETICS:
            sess = SessionAgent(sym).analyse()
            assert sess["tradeable"] is True

        sess_fx = SessionAgent("EURUSD").analyse()
        assert sess_fx["tradeable"] is False
        assert "Dead hours" in sess_fx["reason"]


def test_chief_agent_synthetics_prioritisation():
    context = SymbolContext()
    # Mock context to give a synthetic high confidence so it appears in top3 of brief
    context.last_confidence["PAIN_999"] = 95
    chief = ChiefAgent(context)
    chief.weekday = 5  # Saturday
    decision = chief.decide("full_scan")
    ranked = decision["symbols"]
    priority = decision["priority"]

    for sym in EXPECTED_SYNTHETICS:
        assert priority[sym] >= 0, f"{sym} should not be penalized on weekend"

    # Non-crypto/non-synthetics should have -999 on weekend
    assert priority["EURUSD"] == -999
    assert priority["NASDAQ"] == -999

    # Briefing tags should reflect Synthetic tag
    brief = chief.brief()
    assert "⚡ Synthetic" in brief


def test_data_agent_synthetic_fetch_and_indicators():
    for sym in EXPECTED_SYNTHETICS:
        agent = DataAgent(sym)
        data = agent.fetch(["4h", "1h", "15m", "5m"])
        for tf in ["4h", "1h", "15m", "5m"]:
            assert tf in data
            df = data[tf]
            assert isinstance(df, pd.DataFrame)
            assert not df.empty
            assert len(df) > 50
            for col in ["Open", "High", "Low", "Close", "Volume", "ADX_14", "EMA_20", "EMA_50", "ATRr_14"]:
                assert col in df.columns
                assert not df[col].isna().all()


def test_lifecycle_agent_synthetic_trade_resolution():
    lc = LifecycleAgent()
    now = SAST.localize(datetime(2025, 3, 1, 12, 0))

    # Test trade evaluation with synthetic price resolution
    trade = {
        "id": "syn-test-1",
        "symbol": "PAIN_999",
        "direction": "BUY",
        "entry": 100000.0,
        "sl": 90000.0,
        "tp1": 100005.0,
        "tp2": 100010.0,
        "tp3": 100020.0,
        "opened": "2025-03-01 11:30 SAST",
        "status": "OPEN",
    }

    mock_df = pd.DataFrame({"Close": [100015.0]}, index=[pd.Timestamp.now()])
    with patch.object(DataAgent, "fetch", return_value={"5m": mock_df}):
        changed = lc._check_trade(trade, now)
        assert changed is True
        assert trade["status"] == "CLOSED"
        assert trade["outcome"] in ["TP1", "TP2"]
