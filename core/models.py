"""
OBI v4.2 — Core Data Models
Replaces anonymous payload dicts with typed dataclasses.

Drop this file in: core/models.py
Then import with: from core.models import BiasResult, TriggerResult, ...

Each result class includes a .get(key, default) method so existing
code written for dict-style access (bias.get("factors", [])) keeps
working unmodified while the codebase migrates to attribute access
(bias.factors) over time.
"""

from dataclasses import dataclass, field
from typing import List, Optional


# ─────────────────────────────────────────────
# BIAS
# ─────────────────────────────────────────────

@dataclass
class BiasResult:
    approved:  bool
    direction: str          # "BUY" | "SELL" | "NEUTRAL"
    grade:     str          # "A+" | "A" | "B" | "C" | "D" | "F"
    score:     int          # 0–7 confluence factors
    factors:   List[str]    # e.g. ["HTF bias clear", "MTF aligned"]
    regime:    str          # "TRENDING" | "RANGING" | "VOLATILE"
    reason:    str

    def get(self, key, default=None):
        return getattr(self, key, default)

    @staticmethod
    def blocked(reason: str) -> "BiasResult":
        return BiasResult(
            approved=False, direction="NEUTRAL", grade="F",
            score=0, factors=[], regime="RANGING", reason=reason
        )


# ─────────────────────────────────────────────
# TRIGGER
# ─────────────────────────────────────────────

@dataclass
class TriggerResult:
    fire:       bool
    direction:  str          # "BUY" | "SELL" | "NEUTRAL"
    grade:      str          # "A+" | "A" | "B" | "C" | "F"
    entry:      float
    sl:         float
    tp1:        float
    tp2:        float
    tp3:        float
    rr:         float
    confluence: int
    tags:       List[str]    # ["FVG", "OB", "Momentum", "Discount", "Premium"]
    reason:     str

    def get(self, key, default=None):
        return getattr(self, key, default)

    @staticmethod
    def blocked(reason: str) -> "TriggerResult":
        return TriggerResult(
            fire=False, direction="NEUTRAL", grade="F",
            entry=0.0, sl=0.0, tp1=0.0, tp2=0.0, tp3=0.0,
            rr=0.0, confluence=0, tags=[], reason=reason
        )


# ─────────────────────────────────────────────
# EDGE
# ─────────────────────────────────────────────

@dataclass
class EdgeResult:
    symbol_wr:   float   # win rate % for this symbol
    grade_wr:    float   # win rate % for this signal grade
    regime_wr:   float   # win rate % for this regime
    tag_wr:      float   # win rate % for matching tags
    overall_wr:  float   # win rate % across all closed trades
    sample_size: int
    low_sample:  bool    # True if sample_size < MIN_SAMPLE

    def get(self, key, default=None):
        return getattr(self, key, default)

    @staticmethod
    def default(sample_size: int = 0) -> "EdgeResult":
        return EdgeResult(
            symbol_wr=50.0, grade_wr=50.0, regime_wr=50.0,
            tag_wr=50.0, overall_wr=50.0,
            sample_size=sample_size, low_sample=True
        )


# ─────────────────────────────────────────────
# SCORE
# ─────────────────────────────────────────────

@dataclass
class ScoreResult:
    confidence:    int     # 0–100
    grade:         str     # "A+" | "A" | "B" | "C" | "D"
    risk:          str     # "LOW" | "MEDIUM" | "HIGH"
    bias_score:    int     # 0–100 component score
    trigger_score: int
    regime_score:  int
    edge_score:    int
    session_score: int

    def get(self, key, default=None):
        return getattr(self, key, default)

    @staticmethod
    def default() -> "ScoreResult":
        return ScoreResult(
            confidence=50, grade="C", risk="HIGH",
            bias_score=50, trigger_score=50,
            regime_score=50, edge_score=50, session_score=50
        )


# ─────────────────────────────────────────────
# SIGNAL PAYLOAD  (replaces the payload dict)
# ─────────────────────────────────────────────

@dataclass
class SignalPayload:
    symbol:  str
    bias:    BiasResult
    trigger: TriggerResult
    edge:    EdgeResult
    score:   ScoreResult
    htf:     dict   # HTFAgent output — typed later
    mtf:     dict   # MTFAgent output — typed later
    ltf:     dict   # LTFAgent output — typed later
    zone:    dict   # ZoneAgent output — typed later
    regime:  dict   # RegimeAgent output — typed later
    session: dict   # SessionAgent output — typed later
