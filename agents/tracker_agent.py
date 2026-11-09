"""
OBI Agents — Tracker compatibility adapter.

LifecycleAgent is the sole authority for mutating existing archive records.
This module remains only for callers that still import check_outcome().
"""
from agents.lifecycle_agent import LifecycleAgent


def check_outcome(symbol: str, ticker: str = None):
    """Compatibility wrapper; ticker is ignored in favour of LifecycleAgent routing."""
    return LifecycleAgent().check_open_signals(symbol=symbol)
