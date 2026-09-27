"""Replay case contract.

A replay case contains the exact agent inputs required to reproduce the
HTF -> MTF -> Bias -> Trigger decision path. Market data is optional at this
stage so the same contract can later support raw-market-data replay.
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional


REPLAY_SCHEMA_VERSION = "1.0"
REQUIRED_AGENT_INPUTS = ("htf", "mtf", "ltf", "zone", "session", "regime")


@dataclass(frozen=True)
class ReplayCase:
    case_id: str
    symbol: str
    opened: str
    market_data: Optional[Dict[str, Any]]
    agent_inputs: Dict[str, Any]
    expected: Dict[str, Any]
    schema_version: str = REPLAY_SCHEMA_VERSION

    def validate(self) -> None:
        if not self.case_id:
            raise ValueError("case_id is required")
        if not self.symbol:
            raise ValueError("symbol is required")
        if not self.opened:
            raise ValueError("opened timestamp is required")
        missing = [name for name in REQUIRED_AGENT_INPUTS if name not in self.agent_inputs]
        if missing:
            raise ValueError("missing agent inputs: " + ", ".join(missing))
        if not isinstance(self.expected, dict):
            raise ValueError("expected must be a mapping")

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ReplayCase":
        case = cls(
            case_id=payload.get("case_id", ""),
            symbol=payload.get("symbol", ""),
            opened=payload.get("opened", ""),
            market_data=payload.get("market_data"),
            agent_inputs=payload.get("agent_inputs", {}),
            expected=payload.get("expected", {}),
            schema_version=payload.get("schema_version", REPLAY_SCHEMA_VERSION),
        )
        case.validate()
        return case
