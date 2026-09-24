"""
OBI Agents — Tracker compatibility adapter.

Lifecycle authority was consolidated into LifecycleAgent. This module no
longer contains a second lifecycle engine. The legacy check_outcome()
entry point delegates to the canonical LifecycleAgent for compatibility.
"""
from agents.lifecycle_agent import LifecycleAgent


def check_outcome(symbol: str, ticker: str = None):
    """Compatibility shim; LifecycleAgent is the sole lifecycle engine."""
    print("[TRACKER] Deprecated lifecycle entry point; delegating to LifecycleAgent")
    return LifecycleAgent().check_open_signals()
