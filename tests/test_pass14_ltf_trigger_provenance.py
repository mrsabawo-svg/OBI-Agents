"""Pass 14 — deterministic LTF/Trigger provenance tests.

These tests isolate downstream trigger decisions without changing production
trading logic. They establish the exact blocking conditions used by
TriggerAgent and the direction-preserving conditions used when a trigger fires.
"""

from agents.trigger_agent import TriggerAgent
from core.models import BiasResult


def _bias(direction: str):
    return BiasResult(
        approved=True,
        direction=direction,
        grade="C",
        score=3,
        factors=["x", "y", "z"],
        reason="controlled provenance fixture",
    )


def _ltf(**overrides):
    value = {
        "valid": True,
        "trigger": True,
        "fvg": False,
        "momentum": False,
        "confluence": 1,
        "entry": 100.0,
        "sl": 99.0,
        "tp1": 103.0,
        "tp2": 106.0,
        "tp3": 109.0,
        "rr": 3.0,
    }
    value.update(overrides)
    return value


def test_invalid_ltf_is_terminal_trigger_block():
    result = TriggerAgent("TEST").evaluate(
        _ltf(valid=False),
        {"zone_aligned": True},
        _bias("BUY"),
    )
    assert result.fire is False
    assert result.direction == "NEUTRAL"
    assert result.reason == "LTF trigger not confirmed"


def test_low_rr_is_terminal_trigger_block():
    result = TriggerAgent("TEST").evaluate(
        _ltf(rr=0.43),
        {"zone_aligned": False},
        _bias("SELL"),
    )
    assert result.fire is False
    assert result.direction == "NEUTRAL"
    assert result.reason == "RR too low 0.43"


def test_valid_ltf_preserves_bias_direction_without_zone_alignment():
    result = TriggerAgent("TEST").evaluate(
        _ltf(),
        {"zone_aligned": False},
        _bias("BUY"),
    )
    assert result.fire is True
    assert result.direction == "BUY"
    assert result.rr == 3.0


def test_valid_ltf_preserves_sell_direction():
    result = TriggerAgent("TEST").evaluate(
        _ltf(fvg=True, confluence=2),
        {"zone_aligned": False},
        _bias("SELL"),
    )
    assert result.fire is True
    assert result.direction == "SELL"
