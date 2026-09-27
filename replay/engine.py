"""Deterministic OBI decision-path replay engine."""
from typing import Any, Dict

import pandas as pd

from agents.htf_agent import HTFAgent
from agents.mtf_agent import MTFAgent
from agents.bias_agent import BiasAgent
from agents.trigger_agent import TriggerAgent
from .schema import ReplayCase


def _frame(value: Any):
    """Convert serialized OHLC rows into a DataFrame; preserve DataFrames for tests."""
    if isinstance(value, pd.DataFrame):
        return value
    if isinstance(value, list):
        return pd.DataFrame(value)
    return value


def build_market_data(case: ReplayCase) -> Dict[str, Any]:
    """Deserialize the market_data portion of a replay case."""
    if not case.market_data:
        return {}
    return {timeframe: _frame(rows) for timeframe, rows in case.market_data.items()}


class DirectionReplay:
    """Run the production direction chain without modifying production agents."""

    def run(self, case: ReplayCase) -> Dict[str, Any]:
        case.validate()
        inputs = case.agent_inputs
        market_data = build_market_data(case)

        htf = HTFAgent(case.symbol).analyse(market_data)
        mtf = MTFAgent(case.symbol).analyse(market_data, htf)

        bias = BiasAgent(case.symbol).evaluate(
            htf,
            mtf,
            inputs["session"],
            inputs["regime"],
        )
        trigger = TriggerAgent(case.symbol).evaluate(
            inputs["ltf"],
            inputs["zone"],
            bias,
        )

        result = {
            "case_id": case.case_id,
            "symbol": case.symbol,
            "opened": case.opened,
            "htf": htf,
            "mtf": mtf,
            "bias": {
                "approved": bias.approved,
                "direction": bias.direction,
                "grade": bias.grade,
                "score": bias.score,
                "factors": bias.factors,
                "regime": bias.regime,
                "reason": bias.reason,
            },
            "trigger": {
                "fire": trigger.fire,
                "direction": trigger.direction,
                "grade": trigger.grade,
                "entry": trigger.entry,
                "sl": trigger.sl,
                "tp1": trigger.tp1,
                "tp2": trigger.tp2,
                "tp3": trigger.tp3,
                "rr": trigger.rr,
                "confluence": trigger.confluence,
                "tags": trigger.tags,
                "reason": trigger.reason,
            },
        }
        result["direction_path"] = {
            "htf": htf.get("bias", "NEUTRAL"),
            "mtf": mtf.get("direction", "NEUTRAL"),
            "bias": bias.direction,
            "trigger": trigger.direction,
        }
        return result
