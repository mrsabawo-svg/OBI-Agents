import hashlib
import json

import pandas as pd
import pytest

import agents.edge_agent as edge_module
import agents.intelligence_agent as intelligence_module
from agents.archive_agent import ArchiveAgent
from agents.edge_agent import EdgeAgent
from agents.intelligence_agent import IntelligenceAgent
from core.decision_lineage import (
    AGENT_ORDER,
    AGENT_SPECS,
    build_decision_lineage,
    verify_decision_lineage,
    _sha256,
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
            "schema_version": "obi-data-lineage-v5",
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
    assert 0 < manifest["manifest_content_size_bytes"] < 50000
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
    assert manifest["agent_specs"]["Edge"]["formula_version"] == "1.1.0"
    assert "core/archive_analytics_eligibility.py" in manifest["agent_specs"]["Edge"]["source_files"]
    assert "agents/archive_agent.py" in manifest["agent_specs"]["Edge"]["source_files"]
    assert "agents/lifecycle_agent.py" in manifest["agent_specs"]["Edge"]["source_files"]


def test_decision_lineage_verification_detects_tampering():
    manifest = build_decision_lineage(
        "EURUSD", _market_data(), _outputs(),
        {"status": "complete", "timeframes": {}},
        {"Edge": {"archive_snapshot_sha256": "b" * 64}},
    )
    assert verify_decision_lineage(manifest)
    manifest["agent_traces"]["HTF"]["output"]["bias"] = "BEARISH"
    assert not verify_decision_lineage(manifest)


def test_decision_lineage_detects_nested_tampering_even_if_outer_hash_is_recomputed():
    manifest = build_decision_lineage(
        "EURUSD", _market_data(), _outputs(),
        {"schema_version": "obi-data-lineage-v5", "status": "complete", "timeframes": {}},
        {"Edge": {"archive_snapshot_sha256": "e" * 64}},
    )
    manifest["agent_specs"]["HTF"]["formula_version"] = "tampered"
    material = dict(manifest)
    material.pop("manifest_sha256")
    manifest["manifest_sha256"] = _sha256(material)
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
        {"schema_version": "obi-data-lineage-v5", "status": "complete", "timeframes": {}},
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
            "schema_version": "obi-data-lineage-v5",
            "status": "complete",
            "timeframes": {},
        },
        "decision_lineage": manifest,
    }
    entry = ArchiveAgent()._build_entry(signal)
    assert entry["decision_lineage_status"] == "complete"
    assert entry["decision_lineage_provenance_status"] == "complete"
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
    assert entry["decision_lineage_provenance_status"] == "unavailable"

    manifest = build_decision_lineage(
        "EURUSD", _market_data(), _outputs(),
        {"status": "complete", "timeframes": {}},
        {"Edge": {"archive_snapshot_sha256": "d" * 64}},
    )
    manifest["agent_specs"]["HTF"]["formula_version"] = "tampered"
    signal["decision_lineage"] = manifest
    tampered_entry = ArchiveAgent()._build_entry(signal)
    assert tampered_entry["decision_lineage_status"] == "unavailable"


def test_edge_agent_excludes_legacy_rows_without_verified_analytics_lineage(monkeypatch):
    archive = [
        {
            "status": "CLOSED",
            "symbol": "EURUSD",
            "regime": "TRENDING",
            "tags": ["FVG", "OB"],
            "outcome": "TP1",
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

    assert result.low_sample is True
    assert result.sample_size == 0
    assert agent.archive_eligibility_summary["eligible_count"] == 0
    assert agent.archive_eligibility_summary["excluded_count"] == 3
    assert len(agent.archive_snapshot_sha256) == 64


def _intelligence_payload():
    return {
        "symbol": "EURUSD",
        "trigger": TriggerResult(
            fire=True, direction="BUY", grade="B",
            entry=1.1, sl=1.0, tp1=1.3, tp2=1.4, tp3=1.5,
            rr=2.0, confluence=3, tags=["FVG"], reason="test",
        ),
        "regime": {"label": "TRENDING", "confidence": 0.7},
        "score": {"confidence": 75, "grade": "A"},
        "edge": {"sample_size": 20},
    }


def test_intelligence_agent_archives_before_persisting_or_notifying(monkeypatch):
    events = []

    class FakeReviewer:
        def __init__(self, symbol):
            pass

        def is_duplicate(self, payload):
            return False

        def review(self, payload, accuracy):
            return {"groq_verdict": "PASS", "skeptic_verdict": "PASS"}

        def build_narrative(self, payload):
            return "test narrative"

    class FakeArchive:
        def log(self, payload):
            events.append("archive")
            return True

    class FakePersistence:
        def __init__(self, symbol):
            pass

        def save(self, result, payload):
            events.append("persist")

    class FakeNotifier:
        def __init__(self, symbol):
            pass

        def send(self, result, narrative):
            events.append("notify")

    monkeypatch.setattr(intelligence_module, "SignalReviewer", FakeReviewer)
    monkeypatch.setattr(intelligence_module, "ArchiveAgent", FakeArchive)
    monkeypatch.setattr(intelligence_module, "PersistenceAgent", FakePersistence)
    monkeypatch.setattr(intelligence_module, "NotifierAgent", FakeNotifier)
    monkeypatch.setattr(intelligence_module, "load_memory", lambda: {})

    result = IntelligenceAgent("EURUSD").verdict(_intelligence_payload())
    assert result
    assert events == ["archive", "persist", "notify"]


def test_intelligence_agent_suppresses_notification_when_archive_fails(monkeypatch):
    events = []

    class FakeReviewer:
        def __init__(self, symbol):
            pass

        def is_duplicate(self, payload):
            return False

        def review(self, payload, accuracy):
            return {"groq_verdict": "PASS", "skeptic_verdict": "PASS"}

        def build_narrative(self, payload):
            return "test narrative"

    class FakeArchive:
        def log(self, payload):
            events.append("archive")
            return False

    class FakePersistence:
        def __init__(self, symbol):
            pass

        def save(self, result, payload):
            events.append("persist")

    class FakeNotifier:
        def __init__(self, symbol):
            pass

        def send(self, result, narrative):
            events.append("notify")

    monkeypatch.setattr(intelligence_module, "SignalReviewer", FakeReviewer)
    monkeypatch.setattr(intelligence_module, "ArchiveAgent", FakeArchive)
    monkeypatch.setattr(intelligence_module, "PersistenceAgent", FakePersistence)
    monkeypatch.setattr(intelligence_module, "NotifierAgent", FakeNotifier)
    monkeypatch.setattr(intelligence_module, "load_memory", lambda: {})

    result = IntelligenceAgent("EURUSD").verdict(_intelligence_payload())
    assert result == {}
    assert events == ["archive"]


def test_intelligence_agent_does_not_archive_duplicate(monkeypatch):
    events = []

    class FakeReviewer:
        def __init__(self, symbol):
            pass

        def is_duplicate(self, payload):
            return True

    class FakeArchive:
        def log(self, payload):
            events.append("archive")
            return True

    monkeypatch.setattr(intelligence_module, "SignalReviewer", FakeReviewer)
    monkeypatch.setattr(intelligence_module, "ArchiveAgent", FakeArchive)

    result = IntelligenceAgent("EURUSD").verdict(_intelligence_payload())
    assert result == {}
    assert events == []


def test_decision_lineage_distinguishes_manifest_integrity_from_provenance_completeness():
    manifest = build_decision_lineage(
        "EURUSD",
        _market_data(),
        _outputs(),
        {"schema_version": "obi-data-lineage-v5", "status": "unavailable", "timeframes": {}},
        {},
    )
    assert manifest["status"] == "complete"
    assert manifest["provenance_status"] == "partial"
    assert any("Edge: closed archive snapshot hash unavailable" in item for item in manifest["warnings"])
    assert any("pipeline data lineage is unavailable" in item for item in manifest["warnings"])
    assert verify_decision_lineage(manifest)



def _identity():
    from core.signal_identity import SETUP_IDENTITY_VERSION
    return {
        "signal_id": "EURUSD_20261109_abc123",
        "setup_id": "a" * 64,
        "setup_identity_version": SETUP_IDENTITY_VERSION,
    }


def test_decision_lineage_binds_signal_and_setup_identity():
    identity = _identity()
    manifest = build_decision_lineage(
        "EURUSD", _market_data(), _outputs(),
        {"schema_version": "obi-data-lineage-v5", "status": "complete", "timeframes": {}},
        signal_identity=identity,
    )
    assert manifest["signal_identity"] == identity
    assert verify_decision_lineage(manifest)


def test_decision_lineage_rejects_malformed_signal_identity():
    with pytest.raises(ValueError, match="setup_id"):
        build_decision_lineage(
            "EURUSD", _market_data(), _outputs(),
            {"status": "complete"},
            signal_identity={
                "signal_id": "event-1",
                "setup_id": "not-a-digest",
                "setup_identity_version": "obi-setup-identity-v1",
            },
        )


def test_decision_lineage_verification_detects_identity_tampering():
    manifest = build_decision_lineage(
        "EURUSD", _market_data(), _outputs(),
        {"status": "complete"},
        signal_identity=_identity(),
    )
    manifest["signal_identity"]["signal_id"] = "different-event"
    assert not verify_decision_lineage(manifest)


def test_archive_agent_accepts_matching_identity_bound_lineage():
    from core.signal_identity import SETUP_IDENTITY_VERSION, build_setup_identity
    outputs = _outputs()
    signal = {
        "id": "EURUSD_20261109_abc123",
        "symbol": "EURUSD",
        "trigger": outputs["Trigger"],
        "bias": outputs["Bias"],
        "score": outputs["Score"],
        "regime": outputs["Regime"],
        "htf": outputs["HTF"],
        "mtf": outputs["MTF"],
    }
    setup_id = build_setup_identity(signal)
    signal["decision_lineage"] = build_decision_lineage(
        "EURUSD", _market_data(), outputs,
        {"schema_version": "obi-data-lineage-v5", "status": "complete", "timeframes": {}},
        signal_identity={
            "signal_id": signal["id"],
            "setup_id": setup_id,
            "setup_identity_version": SETUP_IDENTITY_VERSION,
        },
    )
    entry = ArchiveAgent()._build_entry(signal)
    assert entry["decision_lineage_status"] == "complete"
    assert entry["decision_lineage_identity_status"] == "bound"


def test_archive_agent_rejects_lineage_bound_to_another_signal_event():
    from core.signal_identity import SETUP_IDENTITY_VERSION, build_setup_identity
    outputs = _outputs()
    signal = {
        "id": "EURUSD_20261109_actual",
        "symbol": "EURUSD",
        "trigger": outputs["Trigger"],
        "bias": outputs["Bias"],
        "score": outputs["Score"],
        "regime": outputs["Regime"],
        "htf": outputs["HTF"],
        "mtf": outputs["MTF"],
    }
    signal["decision_lineage"] = build_decision_lineage(
        "EURUSD", _market_data(), outputs,
        {"schema_version": "obi-data-lineage-v5", "status": "complete", "timeframes": {}},
        signal_identity={
            "signal_id": "EURUSD_20261109_other",
            "setup_id": build_setup_identity(signal),
            "setup_identity_version": SETUP_IDENTITY_VERSION,
        },
    )
    entry = ArchiveAgent()._build_entry(signal)
    assert entry["decision_lineage_status"] == "unavailable"
    assert entry["decision_lineage_identity_status"] == "mismatch"
    assert entry["decision_lineage_sha256"] is None



def _production_signal_with_lineage(signal_id="EURUSD_20261109_actual", symbol="EURUSD", bound_setup_id=None, manifest_symbol=None):
    from core.signal_identity import SETUP_IDENTITY_VERSION, build_setup_identity

    outputs = _outputs()
    signal = {
        "id": signal_id,
        "symbol": symbol,
        "trigger": outputs["Trigger"],
        "bias": outputs["Bias"],
        "score": outputs["Score"],
        "regime": outputs["Regime"],
        "htf": outputs["HTF"],
        "mtf": outputs["MTF"],
        "decision_lineage_identity_required": True,
    }
    setup_id = bound_setup_id or build_setup_identity(signal)
    signal["decision_lineage"] = build_decision_lineage(
        manifest_symbol or symbol,
        _market_data(),
        outputs,
        {"schema_version": "obi-data-lineage-v5", "status": "complete", "timeframes": {}},
        signal_identity={
            "signal_id": signal_id,
            "setup_id": setup_id,
            "setup_identity_version": SETUP_IDENTITY_VERSION,
        },
    )
    return signal


def test_archive_agent_rejects_unbound_lineage_when_production_identity_is_required():
    signal = _production_signal_with_lineage()
    signal["decision_lineage"] = build_decision_lineage(
        "EURUSD",
        _market_data(),
        _outputs(),
        {"schema_version": "obi-data-lineage-v5", "status": "complete", "timeframes": {}},
    )
    entry = ArchiveAgent()._build_entry(signal)

    assert entry["decision_lineage_status"] == "unavailable"
    assert entry["decision_lineage_identity_status"] == "missing"
    assert entry["decision_lineage_sha256"] is None


def test_archive_agent_rejects_setup_identity_mismatch():
    signal = _production_signal_with_lineage(bound_setup_id="b" * 64)
    entry = ArchiveAgent()._build_entry(signal)

    assert entry["decision_lineage_status"] == "unavailable"
    assert entry["decision_lineage_identity_status"] == "mismatch"
    assert entry["decision_lineage_sha256"] is None


def test_archive_agent_rejects_instrument_identity_mismatch():
    signal = _production_signal_with_lineage(manifest_symbol="GBPUSD")
    entry = ArchiveAgent()._build_entry(signal)

    assert entry["decision_lineage_status"] == "unavailable"
    assert entry["decision_lineage_identity_status"] == "mismatch"
    assert entry["decision_lineage_sha256"] is None
