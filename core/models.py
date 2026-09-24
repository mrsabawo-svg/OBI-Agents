"""OBI v4.2 — Core Data Models
Copyright © Mazvita Sabawo

Typed dataclasses for agent payloads, plus SymbolContext used by
ChiefAgent for session/priority scoring.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class BiasResult:
    approved: bool
    direction: str
    grade: str
    score: int
    factors: List[str]
    regime: str
    reason: str

    @staticmethod
    def blocked(reason: str) -> "BiasResult":
        return BiasResult(False, "NEUTRAL", "F", 0, [], "RANGING", reason)


@dataclass
class TriggerResult:
    fire: bool
    direction: str
    grade: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    rr: float
    confluence: int
    tags: List[str]
    reason: str

    @staticmethod
    def blocked(reason: str) -> "TriggerResult":
        return TriggerResult(False, "NEUTRAL", "F", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, [], reason)


@dataclass
class EdgeResult:
    symbol_wr: float
    selected_wr: float
    regime_wr: float
    tag_wr: float
    overall_wr: float
    sample_size: int
    low_sample: bool
    confidence_interval: tuple = (0.0, 100.0)
    evidence_level: str = "E5"
    lookup_level: str = "BASE_RATE"

    @staticmethod
    def default(sample_size: int = 0) -> "EdgeResult":
        return EdgeResult(
            symbol_wr=50.0, selected_wr=50.0, regime_wr=50.0,
            tag_wr=50.0, overall_wr=50.0,
            sample_size=sample_size, low_sample=True,
            confidence_interval=(0.0, 100.0),
            evidence_level="E5",
            lookup_level="BASE_RATE"
        )


@dataclass
class ScoreResult:
    confidence: int
    grade: str
    risk: str
    bias_score: int
    trigger_score: int
    regime_score: int
    edge_score: int
    session_score: int

    @staticmethod
    def default() -> "ScoreResult":
        return ScoreResult(50, "C", "HIGH", 50, 50, 50, 50, 50)


@dataclass
class SignalPayload:
    symbol: str
    bias: BiasResult
    trigger: TriggerResult
    edge: EdgeResult
    score: ScoreResult
    htf: dict
    mtf: dict
    ltf: dict
    zone: dict
    regime: dict
    session: dict


@dataclass
class SymbolContext:
    last_confidence: dict = field(default_factory=dict)
    last_signal: dict = field(default_factory=dict)

    @classmethod
    def from_memory(cls, memory: dict, symbols: list) -> "SymbolContext":
        last_confidence = {}
        last_signal = {}
        for sym in symbols:
            sym_data = memory.get(sym)
            if not isinstance(sym_data, dict):
                continue
            if "last_confidence" in sym_data:
                last_confidence[sym] = sym_data["last_confidence"]
            if "last_signal" in sym_data:
                last_signal[sym] = sym_data["last_signal"]
        return cls(last_confidence=last_confidence, last_signal=last_signal)


def _payload_to_dict(payload: dict) -> dict:
    trigger = payload["trigger"]
    bias = payload["bias"]
    edge = payload["edge"]
    score = payload["score"]
    return {
        "symbol": payload.get("symbol", ""),
        "trigger": {
            "direction": trigger.direction, "grade": trigger.grade,
            "entry": trigger.entry, "sl": trigger.sl, "tp1": trigger.tp1,
            "tp2": trigger.tp2, "tp3": trigger.tp3, "rr": trigger.rr,
            "confluence": trigger.confluence, "tags": trigger.tags,
        },
        "bias": {
            "direction": bias.direction, "grade": bias.grade,
            "score": bias.score, "factors": bias.factors, "regime": bias.regime,
        },
        "edge": {
            "symbol_wr": edge.symbol_wr, "selected_wr": edge.selected_wr,
            "regime_wr": edge.regime_wr, "tag_wr": edge.tag_wr,
            "overall_wr": edge.overall_wr, "sample_size": edge.sample_size,
            "low_sample": edge.low_sample,
            "confidence_interval": edge.confidence_interval,
            "evidence_level": edge.evidence_level,
            "lookup_level": edge.lookup_level,
        },
        "score": {
            "confidence": score.confidence, "grade": score.grade,
            "risk": score.risk, "bias_score": score.bias_score,
            "trigger_score": score.trigger_score, "regime_score": score.regime_score,
            "edge_score": score.edge_score, "session_score": score.session_score,
        },
        "regime": payload.get("regime", {}),
        "session": payload.get("session", {}),
    }
