import hashlib
import json

import pandas as pd
import pytest

import agents.edge_agent as edge_module
from agents.archive_agent import ArchiveAgent
from agents.edge_agent import EdgeAgent
from core.decision_lineage import (
    AGENT_ORDER,
    AGENT_SPECS,
    build_decision_lineage,
    verify_decision_lineage,
)
from core.models import BiasResult, EdgeResult, ScoreResult, TriggerResult


def _market_data():
    index = pd.date_range("2026-01-01", periods=4, freq="h", tz="UTC")
    frame = pd.DataFrame(
        {
            "Open": [1.0, 1.1, 1.2, 1.3],
            "High": [1.2, 1.3, 1.4, 1.5],
            "Low": [0.9, 1.0, 1.1, 1.2],
            "Close": [1.1, 1.2, 1.3, 1.4],
            "EMA_20": [1.0, 1.05, 1.1, 1.2],
            "EMA_50": [0.9, 0.95, 1.0, 1.1],
            "ATR_14": [0.1, 0.1, 0.1, 0.1],
        },
        index=index,
    )
    return {timeframe: frame.copy() for timeframe in ("4h", "1h", "15m", "5m")}


def _outputs():
    return {
        "Session": {
            "tradeable": True, "reason": "test session",
            "kill_zone": True, "zone_name": "London Open", "peak": True,
        },
        "HTF": {
            "bias": "BULLISH", "confidence": 0.8,
            "bull_score": 4, "bear_score": 1,
        },
        "Regime": {
            "state": 1, "label": "TRENDING",
            "confidence": 0.75, "raw_state": 2,
        },
        "MTF": {
            "aligned": True, "bos": True, "sweep": False,
            "order_block": True, "confluence": 2, "direction": "BUY",
        },
        "Bias": BiasResult(
            approved=True, direction="BUY", grade="B", score=4,
            factors=["HTF bias clear", "MTF aligned", "MTF BOS confirmed", "Order block present"],
            regime="TRENDING", reason="4/7 factors",
        ),
        "Zone": {
            "current_price": 1.4, "zone_aligned": True,
            "support_1h": 1.2, "resistance_1h": 1.5,
        },
        "LTF": {
            "trigger": True, "fvg": True, "momentum": False,
            "confluence": 2, "entry": 1.4, "sl": 1.2,
            "tp1": 1.5, "tp2": 1.6, "tp3": 1.7,
            "rr": 0.5, "atr": 0.1, "direction": "BUY", "valid": True,
        },
        "Trigger": TriggerResult(
            fire=True, direction="BUY", grade="C",
            entry=1.4, sl=1.2, tp1=1.5, tp2=1.6, tp3=1.7,
            rr=0.5, confluence=3, tags=["FVG", "OB"],
            reason="Grade C setup",
        ),
        "Edge": EdgeResult(
            symbol_wr=52.0, grade_wr=50.0, regime_wr=54.0,
            tag_wr=58.0, overall_wr=51.0, sample_size=22,
            low_sample=False,
        ),
        "Score": ScoreResult(
            confidence=67, grade="B", risk="HIGH",
            bias_score=57, trigger_score=40, regime_score=75,
            edge_score=52, session_score=100,
        ),
    }


def test_decision_lineage_versions_every_decision_agent_and_is_self_verifying():
    manifest = build_decision_lineage(
        symbol="EURUSD",
        market_data=_market_data(),
        agent_outputs=_outputs(),
        data_lineage={
            "schema_version": "obi-data-lineage-v3",
            "status": "complete",
            "timeframes": {},
        },
        external_inputs={"Edge": {"archive_snapshot_sha256": "a" * 64}},
    )

    assert manifest["schema_version"] == "obi-decision-lineage-v1"
    assert manifest["status"] == "complete"
    assert manifest["provenance_status"] == "complete"
    assert manifest["agent_order"] == list(AGENT_ORDER)
    assert set(manifest["agent_specs"]) == set(AGENT_ORDER)
    assert set(manifest["agent_traces"]) == set(AGENT_ORDER)
    assert manifest["manifest_content_size_bytes"] > 0
    assert verify_decision_lineage(manifest)

    for name in AGENT_ORDER:
        spec = manifest["agent_specs"][name]
        trace = manifest["agent_traces"][name]
        assert spec["formula_id"]
        assert spec["formula_version"]
        assert spec["formula"]
        assert spec["parameters"]
        assert spec["inputs"]
        assert spec["outputs"]
        assert len(spec["formula_spec_sha256"]) == 64
        assert len(trace["implementation_sha256"]) == 64
        assert len(trace["input_snapshot_sha256"]) == 64
        assert len(trace["output_sha256"]) == 64
        assert trace["output"] is not None

    assert manifest["agent_traces"]["Edge"]["input_snapshot"]["external_inputs"][
        "archive_snapshot_sha256"
    ] == "a" * 64
    assert manifest["market_data"]["1h"]["row_count"] == 4


def test_decision_lineage_verification_detects_tampering():
    manifest = build_decision_lineage(
        "EURUSD", _market_data(), _outputs(),
        {"status": "complete", "timeframes": {}},
        {"Edge": {"archive_snapshot_sha256": "b" * 64}},
    )
    assert verify_decision_lineage(manifest)
    manifest["agent_traces"]["HTF"]["output"]["bias"] = "BEARISH"
    assert not verify_decision_lineage(manifest)


def test_decision_lineage_requires_every_agent_output():
    outputs = _outputs()
    outputs.pop("Score")
    with pytest.raises(ValueError, match="Score"):
        build_decision_lineage(
            "EURUSD", _market_data(), outputs,
            {"status": "complete"}, {},
        )


def test_agent_formula_specs_are_complete_and_reference_existing_sources():
    from pathlib import Path
    import core.decision_lineage as module

    root = Path(module.__file__).resolve().parents[1]
    assert set(AGENT_SPECS) == set(AGENT_ORDER)
    for spec in AGENT_SPECS.values():
        assert spec["agent_version"]
        assert spec["formula_id"]
        assert spec["formula_version"]
        assert spec["formula"]
        assert spec["parameters"]
        assert spec["inputs"]
        assert spec["outputs"]
        for relative_path in spec["source_files"]:
            assert (root / relative_path).is_file()


def test_archive_agent_preserves_verified_decision_lineage():
    manifest = build_decision_lineage(
        "EURUSD", _market_data(), _outputs(),
        {"schema_version": "obi-data-lineage-v3", "status": "complete", "timeframes": {}},
        {"Edge": {"archive_snapshot_sha256": "c" * 64}},
    )
    outputs = _outputs()
    signal = {
        "symbol": "EURUSD",
        "trigger": outputs["Trigger"],
        "bias": outputs["Bias"],
        "score": outputs["Score"],
        "regime": outputs["Regime"],
        "htf": outputs["HTF"],
        "mtf": outputs["MTF"],
        "data_lineage": {
            "schema_version": "obi-data-lineage-v3",
            "status": "complete",
            "timeframes": {},
        },
        "decision_lineage": manifest,
    }
    entry = ArchiveAgent()._build_entry(signal)
    assert entry["decision_lineage_status"] == "complete"
    assert entry["decision_lineage_sha256"] == manifest["manifest_sha256"]
    assert entry["decision_lineage"] == manifest


def test_archive_agent_marks_missing_or_tampered_decision_lineage_unavailable():
    outputs = _outputs()
    signal = {
        "symbol": "EURUSD",
        "trigger": outputs["Trigger"],
        "bias": outputs["Bias"],
        "score": outputs["Score"],
        "regime": outputs["Regime"],
        "htf": outputs["HTF"],
        "mtf": outputs["MTF"],
    }
    entry = ArchiveAgent()._build_entry(signal)
    assert entry["decision_lineage_status"] == "unavailable"
    assert entry["decision_lineage_sha256"] is None

    manifest = build_decision_lineage(
        "EURUSD", _market_data(), _outputs(),
        {"status": "complete", "timeframes": {}},
        {"Edge": {"archive_snapshot_sha256": "d" * 64}},
    )
    manifest["agent_specs"]["HTF"]["formula_version"] = "tampered"
    signal["decision_lineage"] = manifest
    tampered_entry = ArchiveAgent()._build_entry(signal)
    assert tampered_entry["decision_lineage_status"] == "unavailable"


def test_edge_agent_hashes_the_exact_archive_fields_it_uses(monkeypatch):
    archive = [
        {
            "status": "CLOSED",
            "symbol": "EURUSD",
            "regime": "TRENDING",
            "tags": ["FVG", "OB"],
            "outcome": "TP1",
            "unused_display_field": "not consumed by EdgeAgent",
        },
        {
            "status": "CLOSED",
            "symbol": "GBPUSD",
            "regime": "RANGING",
            "tags": ["Momentum"],
            "outcome": "SL",
        },
        {
            "status": "OPEN",
            "symbol": "EURUSD",
            "regime": "TRENDING",
            "tags": ["FVG"],
            "outcome": "PENDING",
        },
    ]
    monkeypatch.setattr(edge_module, "load_memory", lambda: {"_archive": archive})
    agent = EdgeAgent("EURUSD")
    result = agent.analyse(
        _outputs()["Trigger"],
        _outputs()["Bias"],
        {"label": "TRENDING"},
    )

    projected = [
        {
            "status": row.get("status"),
            "symbol": row.get("symbol"),
            "regime": row.get("regime"),
            "tags": row.get("tags", []),
            "outcome": row.get("outcome"),
        }
        for row in archive
        if row.get("status") == "CLOSED" and row.get("outcome") != "EXPIRED"
    ]
    expected = hashlib.sha256(
        json.dumps(
            projected, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    ).hexdigest()
    assert result.low_sample is True
    assert result.sample_size == 2
    assert agent.archive_snapshot_sha256 == expected
