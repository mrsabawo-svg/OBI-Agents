"""
OBI Agents - Intelligence Agent
Thin orchestrator. Delegates to SignalReviewer, PersistenceAgent, NotifierAgent.
"""
from core.utils import sast_str
from core.memory import load as load_memory
from agents.signal_reviewer import SignalReviewer
from agents.persistence_agent import PersistenceAgent
from agents.notifier_agent import NotifierAgent


class IntelligenceAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def verdict(self, payload: dict) -> dict:
        print("[INTEL] " + self.symbol + ": starting")

        reviewer = SignalReviewer(self.symbol)

        if reviewer.is_duplicate(payload):
            print("[INTEL] " + self.symbol + ": DUPLICATE BLOCKED")
            return {}

        memory   = load_memory()
        accuracy = memory.get(self.symbol, {}).get("accuracy", "No history yet")

        review    = reviewer.review(payload, accuracy)
        narrative = reviewer.build_narrative(payload)

        trigger = payload["trigger"]
        result = {
            "symbol":        self.symbol,
            "timestamp":     sast_str(),
            "direction":     trigger.direction,
            "grade":         trigger.grade,
            "entry":         trigger.entry,
            "sl":            trigger.sl,
            "tp1":           trigger.tp1,
            "tp2":           trigger.tp2,
            "tp3":           trigger.tp3,
            "rr":            trigger.rr,
            "tags":          trigger.tags,
            "groq_verdict":  review["groq_verdict"],
            "devil_verdict": review["devil_verdict"],
            "regime":        payload.get("regime", {}),
            "score":         payload.get("score", {}),
            "edge":          payload.get("edge", {}),
        }

        PersistenceAgent(self.symbol).save(result, payload)
        NotifierAgent(self.symbol).send(result, narrative)

        return result
