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
from core.data_lineage import build_data_lineage
from core.models import SymbolContext

SAST = pytz.timezone("Africa/Johannesburg")

EXPECTED_SYNTHETICS = {"PAIN_999", "GAIN_999", "PAIN_1200", "GAIN_1200", "SFX_VOL_75"}


def canonical_receipt(rows, report_id):
    eligible = [row for row in rows if not row.get("quarantine_reasons")]
    lineage = [
        {"observation_id": row["observation_id"], "candle_id": f"candle-{row['observation_id']}"}
        for row in eligible
    ]
    return {
        "persisted": True,
        "inserted_count": len(lineage),
        "candle_ids": [link["candle_id"] for link in lineage],
        "source_lineage": lineage,
        "validation_report_id": report_id,
        "validation_report_sha256": "a" * 64,
    }



def _complete_history_report(rows, timeframe="5m", start_pos=0):
    forming_indexes = [
        index for index, row in enumerate(rows)
        if row.get("bar_status") == "forming"
    ]
    return {
        "schema_version": "obi-history-completeness-v1",
        "status": "complete",
        "session_model": "unknown",
        "coverage_scope": "requested_window",
        "requested_start_pos": start_pos,
        "timeframe": timeframe,
        "instrument_id": "SFX_VOL_75",
        "broker_symbol": "SFX Vol 75",
        "account_fingerprint": "a" * 64,
        "requested_count": len(rows),
        "returned_count": len(rows),
        "valid_timestamp_count": len(rows),
        "invalid_timestamp_count": 0,
        "missing_interval_count": 0,
        "gap_segment_count": 0,
        "duplicate_timestamp_count": 0,
        "duplicate_observation_count": 0,
        "duplicate_group_count": 0,
        "gaps": [],
        "out_of_order_count": 0,
        "timeframe_alignment_error_count": 0,
        "future_bar_count": 0,
        "latest_bar_stale": False,
        "freshness_status": "fresh",
        "snapshot_stable": True,
        "snapshot_check_performed": False,
        "forming_observation_indexes": forming_indexes,
        "pagination": {
            "page_count": 1,
            "page_sizes": [len(rows)],
            "page_start_positions": [start_pos],
        },
        "reasons": [],
        "forming_count": len(forming_indexes),
        "closed_count": len(rows) - len(forming_indexes),
    }


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



def test_synthetic_session_does_not_depend_on_forex_clock_or_kill_zone():
    with patch("agents.session_agent.now_sast", side_effect=RuntimeError("clock unavailable")), \
         patch("agents.session_agent.is_kill_zone", side_effect=RuntimeError("kill-zone unavailable")):
        for sym in EXPECTED_SYNTHETICS:
            session = SessionAgent(sym).analyse()
            assert session["tradeable"] is True
            assert session["reason"] == "Synthetic — 24/7 tradeable"
            assert session["kill_zone"] is False


def test_chief_agent_synthetics_prioritisation():
    context = SymbolContext()
    # Mock context to give a synthetic top confidence so it appears in top3 of brief
    context.last_confidence["PAIN_999"] = 100
    # Freeze time during Asian Session when SFX_VOL_75 is also in SESSION_PRIORITY
    fixed_time = SAST.localize(datetime(2025, 3, 1, 3, 0))  # Saturday 03:00 SAST
    with patch("agents.chief_agent.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_time
        mock_dt.strptime = datetime.strptime
        chief = ChiefAgent(context)
        chief.weekday = 5  # Saturday
        chief.hour = 3
        chief.now = fixed_time
        decision = chief.decide("full_scan")
        ranked = decision["symbols"]
        priority = decision["priority"]

        for sym in EXPECTED_SYNTHETICS:
            assert priority[sym] >= 0, f"{sym} should not be penalized on weekend"

        # Non-crypto/non-synthetics should have -999 on weekend
        assert priority["EURUSD"] == -999
        assert priority["NASDAQ"] == -999

        # Briefing tags should reflect Synthetic tag when synthetic is in top3
        # Since PAIN_999 has conf=100 (+30pts) and SFX_VOL_75 is in Asian session (+10pts), synthetics rank high
        # Give SFX_VOL_75 top confidence as well
        context.last_confidence["SFX_VOL_75"] = 100
        brief = chief.brief()
        assert "⚡ Synthetic" in brief


def test_data_agent_refuses_simulated_synthetic_fallback(monkeypatch):
    monkeypatch.delenv("MT5_BRIDGE_URL", raising=False)
    data = DataAgent("SFX_VOL_75").fetch(["5m"])
    assert data == {}


def test_data_agent_accepts_valid_live_bridge_candles(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_URL", "http://mt5-bridge.test")

    timestamps = pd.date_range("2026-11-08T00:00:00Z", periods=80, freq="5min")
    rows = []
    price = 5000.0
    for index, timestamp in enumerate(timestamps):
        rows.append({
            "observation_id": f"obs-{index}",
            "timestamp": timestamp.isoformat(),
            "Open": price,
            "High": price + 2,
            "Low": price - 2,
            "Close": price + 1,
            "Volume": 100,
            "volume_type": "tick_volume",
            "bar_status": "closed", "quarantine_reasons": [],
        })
        price += 1

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "ok", "symbol_id": "SFX_VOL_75", "timeframe": "5m",
                "broker": "Weltrade", "broker_symbol": "SFX Vol 75",
                "server_name": "Weltrade-Demo", "account_environment": "demo",
                "source_type": "broker_bars",
                "platform": "MT5",
                "instrument_verification_status": "verified",
                "timeframe_verified": True,
                "analysis_eligible": True,
                "account_identity": {
                    "account_fingerprint": "a" * 64,
                    "identity_pinned": True,
                    "broker_company": "Weltrade Ltd",
                    "server": "Weltrade-Demo",
                    "environment": "demo",
                    "platform": "MT5",
                },
                "raw_persistence": {
                    "persisted": True, "account_fingerprint": "a" * 64, "batch_id": "test-batch",
                    "inserted_count": len(rows), "quarantine_count": 0,
                    "observation_ids": [f"obs-{i}" for i in range(len(rows))],
                    "quarantine_ids": [],
                    "quarantine_records": [],
                },
                "quality_report": {
                    "report_id": "accepted-report",
                    "account_fingerprint": "a" * 64,
                    "history_completeness": _complete_history_report(rows),
                    "received_count": len(rows), "quarantined_observation_count": 0,
                    "quarantine_record_count": 0, "reason_counts": {},
                },
                "canonical_persistence": canonical_receipt(rows, "accepted-report"),
                "data": rows,
            }

    with patch("agents.data_agent.requests.get", return_value=FakeResponse()):
        data = DataAgent("SFX_VOL_75").fetch(["5m"])

    assert "5m" in data
    assert not data["5m"].empty
    assert {"Open", "High", "Low", "Close", "Volume"}.issubset(data["5m"].columns)
    assert {"CanonicalCandleID", "RawObservationID"}.issubset(data["5m"].columns)
    assert data["5m"]["CanonicalCandleID"].notna().all()
    assert data["5m"]["RawObservationID"].notna().all()
    lineage = build_data_lineage(data, "SFX_VOL_75", expected_timeframes=["5m"])
    assert lineage["status"] == "complete"
    assert lineage["timeframes"]["5m"]["validation_report_id"] == "accepted-report"
    assert lineage["timeframes"]["5m"]["account_fingerprint"] == "a" * 64
    assert lineage["timeframes"]["5m"]["quality_summary"]["received_count"] == 80
    assert lineage["timeframes"]["5m"]["quality_summary"]["durable_quarantined_count"] == 0
    assert lineage["timeframes"]["5m"]["feature_lineage_status"] == "complete"
    assert lineage["feature_lineage_status"] == "complete"
    assert lineage["timeframes"]["5m"]["feature_lineage"]["feature_set_version"] == "data-agent-indicators-v1"


def test_data_agent_rejects_missing_canonical_lineage(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_URL", "http://mt5-bridge.test")
    rows = [{
        "observation_id": "obs-1",
        "timestamp": "2026-11-08T00:00:00Z",
        "Open": 5000, "High": 5002, "Low": 4998, "Close": 5001,
        "Volume": 100, "volume_type": "tick_volume",
        "bar_status": "closed", "quarantine_reasons": [],
    }]

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "ok", "symbol_id": "SFX_VOL_75", "timeframe": "5m",
                "broker": "Weltrade", "broker_symbol": "SFX Vol 75",
                "server_name": "Weltrade-Demo", "account_environment": "demo",
                "source_type": "broker_bars",
                "platform": "MT5",
                "instrument_verification_status": "verified",
                "timeframe_verified": True,
                "analysis_eligible": True,
                "account_identity": {
                    "account_fingerprint": "a" * 64,
                    "identity_pinned": True,
                    "broker_company": "Weltrade Ltd",
                    "server": "Weltrade-Demo",
                    "environment": "demo",
                    "platform": "MT5",
                },
                "raw_persistence": {
                    "persisted": True, "account_fingerprint": "a" * 64, "batch_id": "lineage-test",
                    "inserted_count": 1, "quarantine_count": 0,
                    "observation_ids": ["obs-1"], "quarantine_ids": [],
                    "quarantine_records": [],
                },
                "quality_report": {
                    "report_id": "lineage-report",
                    "account_fingerprint": "a" * 64,
                    "history_completeness": _complete_history_report(rows), "received_count": 1,
                    "quarantined_observation_count": 0,
                    "quarantine_record_count": 0, "reason_counts": {},
                },
                "canonical_persistence": {
                    "persisted": True, "inserted_count": 0,
                    "candle_ids": [], "source_lineage": [],
                    "validation_report_id": "lineage-report",
                    "validation_report_sha256": "a" * 64,
                },
                "data": rows,
            }

    with patch("agents.data_agent.requests.get", return_value=FakeResponse()):
        data = DataAgent("SFX_VOL_75").fetch(["5m"])

    assert data == {}


def test_data_agent_rejects_invalid_bridge_ohlc(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_URL", "http://mt5-bridge.test")

    rows = [{
        "observation_id": "obs-0",
        "timestamp": "2026-11-08T00:00:00Z",
        "Open": 5000,
        "High": 4990,
        "Low": 5010,
        "Close": 5005,
        "Volume": 100,
        "volume_type": "tick_volume",
        "bar_status": "closed",
        "quarantine_reasons": [],
    }]

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "ok", "symbol_id": "SFX_VOL_75", "timeframe": "5m",
                "broker": "Weltrade", "broker_symbol": "SFX Vol 75",
                "server_name": "Weltrade-Demo", "account_environment": "demo",
                "source_type": "broker_bars",
                "platform": "MT5",
                "instrument_verification_status": "verified",
                "timeframe_verified": True,
                "analysis_eligible": True,
                "account_identity": {
                    "account_fingerprint": "a" * 64,
                    "identity_pinned": True,
                    "broker_company": "Weltrade Ltd",
                    "server": "Weltrade-Demo",
                    "environment": "demo",
                    "platform": "MT5",
                },
                "raw_persistence": {
                    "persisted": True, "account_fingerprint": "a" * 64, "batch_id": "test-batch",
                    "inserted_count": len(rows), "quarantine_count": 0,
                    "observation_ids": [f"obs-{i}" for i in range(len(rows))],
                    "quarantine_ids": [], "quarantine_records": [],
                },
                "quality_report": {
                    "report_id": "invalid-report",
                    "account_fingerprint": "a" * 64,
                    "history_completeness": _complete_history_report(rows),
                    "received_count": len(rows), "quarantined_observation_count": 0,
                    "quarantine_record_count": 0, "reason_counts": {},
                },
                "canonical_persistence": canonical_receipt(rows, "invalid-report"),
                "data": rows,
            }

    with patch("agents.data_agent.requests.get", return_value=FakeResponse()):
        data = DataAgent("SFX_VOL_75").fetch(["5m"])

    assert data == {}



def test_data_agent_excludes_forming_bridge_bar(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_URL", "http://mt5-bridge.test")
    timestamps = pd.date_range("2026-11-08T00:00:00Z", periods=80, freq="5min")
    rows = []
    for index, timestamp in enumerate(timestamps):
        price = 5000 + index
        rows.append({
            "observation_id": f"obs-{index}",
            "timestamp": timestamp.isoformat(), "Open": price,
            "High": price + 2, "Low": price - 2, "Close": price + 1,
            "Volume": 100, "volume_type": "tick_volume",
            "bar_status": "forming" if index == 79 else "closed",
            "quarantine_reasons": [],
        })

    class FakeResponse:
        def raise_for_status(self):
            return None
        def json(self):
            return {
                "status": "ok", "symbol_id": "SFX_VOL_75", "timeframe": "5m",
                "broker": "Weltrade", "broker_symbol": "SFX Vol 75",
                "server_name": "Weltrade-Demo", "account_environment": "demo",
                "source_type": "broker_bars",
                "platform": "MT5",
                "instrument_verification_status": "verified",
                "timeframe_verified": True,
                "analysis_eligible": True,
                "account_identity": {
                    "account_fingerprint": "a" * 64,
                    "identity_pinned": True,
                    "broker_company": "Weltrade Ltd",
                    "server": "Weltrade-Demo",
                    "environment": "demo",
                    "platform": "MT5",
                },
                "raw_persistence": {
                    "persisted": True, "account_fingerprint": "a" * 64, "batch_id": "test-batch",
                    "inserted_count": len(rows), "quarantine_count": 0,
                    "observation_ids": [f"obs-{i}" for i in range(len(rows))],
                    "quarantine_ids": [], "quarantine_records": [],
                },
                "quality_report": {
                    "report_id": "forming-report",
                    "account_fingerprint": "a" * 64,
                    "history_completeness": _complete_history_report(rows),
                    "received_count": len(rows), "quarantined_observation_count": 0,
                    "quarantine_record_count": 0, "reason_counts": {},
                },
                "canonical_persistence": canonical_receipt(rows, "forming-report"),
                "data": rows,
            }

    with patch("agents.data_agent.requests.get", return_value=FakeResponse()):
        data = DataAgent("SFX_VOL_75").fetch(["5m"])
    assert "5m" in data
    assert len(data["5m"]) == 79
    assert data["5m"].attrs["quality_report"]["forming_excluded_count"] == 1


def test_data_agent_withholds_data_without_raw_persistence(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_URL", "http://mt5-bridge.test")

    class FakeResponse:
        def raise_for_status(self):
            return None
        def json(self):
            return {
                "status": "ok", "symbol_id": "SFX_VOL_75", "timeframe": "5m",
                "broker": "Weltrade", "broker_symbol": "SFX Vol 75",
                "server_name": "Weltrade-Demo", "account_environment": "demo",
                "source_type": "broker_bars", "platform": "MT5",
                "instrument_verification_status": "verified",
                "timeframe_verified": True,
                "analysis_eligible": True,
                "account_identity": {
                    "account_fingerprint": "a" * 64,
                    "identity_pinned": True,
                    "broker_company": "Weltrade Ltd",
                    "server": "Weltrade-Demo",
                    "environment": "demo",
                    "platform": "MT5",
                },
                "data": [],
            }

    with patch("agents.data_agent.requests.get", return_value=FakeResponse()):
        data = DataAgent("SFX_VOL_75").fetch(["5m"])
    assert data == {}


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


def test_data_agent_excludes_durably_quarantined_rows(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_URL", "http://mt5-bridge.test")
    timestamps = pd.date_range("2026-11-08T00:00:00Z", periods=80, freq="5min")
    rows = []
    for index, timestamp in enumerate(timestamps):
        price = 5000 + index
        rows.append({
            "observation_id": f"obs-{index}",
            "timestamp": timestamp.isoformat(), "Open": price,
            "High": price + 2, "Low": price - 2, "Close": price + 1,
            "Volume": 100, "volume_type": "tick_volume",
            "bar_status": "closed",
            "quarantine_reasons": ["INVALID_OHLC"] if index == 10 else [],
        })

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "ok", "symbol_id": "SFX_VOL_75", "timeframe": "5m",
                "broker": "Weltrade", "broker_symbol": "SFX Vol 75",
                "server_name": "Weltrade-Demo", "account_environment": "demo",
                "source_type": "broker_bars",
                "platform": "MT5",
                "instrument_verification_status": "verified",
                "timeframe_verified": True,
                "analysis_eligible": True,
                "account_identity": {
                    "account_fingerprint": "a" * 64,
                    "identity_pinned": True,
                    "broker_company": "Weltrade Ltd",
                    "server": "Weltrade-Demo",
                    "environment": "demo",
                    "platform": "MT5",
                },
                "raw_persistence": {
                    "persisted": True, "account_fingerprint": "a" * 64, "batch_id": "quarantine-test",
                    "inserted_count": len(rows), "quarantine_count": 1,
                    "observation_ids": [f"obs-{i}" for i in range(len(rows))],
                    "quarantine_ids": ["quarantine-0"],
                    "quarantine_records": [{
                        "quarantine_id": "quarantine-0",
                        "observation_id": "obs-10",
                        "reason_code": "INVALID_OHLC",
                    }],
                },
                "quality_report": {
                    "report_id": "quarantine-report",
                    "account_fingerprint": "a" * 64,
                    "history_completeness": _complete_history_report(rows),
                    "received_count": len(rows), "quarantined_observation_count": 1,
                    "quarantine_record_count": 1, "reason_counts": {"INVALID_OHLC": 1},
                },
                "canonical_persistence": canonical_receipt(rows, "quarantine-report"),
                "data": rows,
            }

    with patch("agents.data_agent.requests.get", return_value=FakeResponse()):
        data = DataAgent("SFX_VOL_75").fetch(["5m"])

    assert "5m" in data
    assert len(data["5m"]) == 79
    assert data["5m"].attrs["quality_report"]["durable_quarantined_count"] == 1


def test_data_agent_withholds_data_when_quarantine_counts_disagree(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_URL", "http://mt5-bridge.test")
    rows = [{
        "observation_id": "obs-0",
        "timestamp": "2026-11-08T00:00:00Z", "Open": 5000,
        "High": 5002, "Low": 4998, "Close": 5001, "Volume": 100,
        "volume_type": "tick_volume", "bar_status": "closed",
        "quarantine_reasons": ["INVALID_OHLC"],
    }]

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "ok", "symbol_id": "SFX_VOL_75", "timeframe": "5m",
                "broker": "Weltrade", "broker_symbol": "SFX Vol 75",
                "server_name": "Weltrade-Demo", "account_environment": "demo",
                "source_type": "broker_bars",
                "platform": "MT5",
                "instrument_verification_status": "verified",
                "timeframe_verified": True,
                "analysis_eligible": True,
                "account_identity": {
                    "account_fingerprint": "a" * 64,
                    "identity_pinned": True,
                    "broker_company": "Weltrade Ltd",
                    "server": "Weltrade-Demo",
                    "environment": "demo",
                    "platform": "MT5",
                },
                "raw_persistence": {
                    "persisted": True, "account_fingerprint": "a" * 64, "batch_id": "mismatch-test",
                    "inserted_count": 1, "quarantine_count": 0,
                    "observation_ids": ["obs-0"], "quarantine_ids": [],
                    "quarantine_records": [],
                },
                "quality_report": {
                    "report_id": "mismatch-report",
                    "account_fingerprint": "a" * 64,
                    "history_completeness": _complete_history_report(rows),
                    "received_count": 1, "quarantined_observation_count": 0,
                    "quarantine_record_count": 0, "reason_counts": {},
                },
                "canonical_persistence": canonical_receipt(rows, "mismatch-report"),
                "data": rows,
            }

    with patch("agents.data_agent.requests.get", return_value=FakeResponse()):
        data = DataAgent("SFX_VOL_75").fetch(["5m"])
    assert data == {}

def test_data_agent_withholds_unverified_instrument_even_with_raw_evidence(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_URL", "http://mt5-bridge.test")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "ok",
                "symbol_id": "SFX_VOL_75",
                "timeframe": "5m",
                "raw_persistence": {"persisted": True},
                "account_identity": {
                    "account_fingerprint": "a" * 64,
                    "identity_pinned": True,
                    "broker_company": "Weltrade Ltd",
                    "server": "Weltrade-Demo",
                    "environment": "demo",
                    "platform": "MT5",
                },
                "instrument_verification_status": "partially_verified",
                "timeframe_verified": False,
                "analysis_eligible": False,
            }

    with patch("agents.data_agent.requests.get", return_value=FakeResponse()):
        data = DataAgent("SFX_VOL_75").fetch(["5m"])

    assert data == {}
