"""
OBI Agents - Intelligence Agent
Thin orchestrator. Delegates to SignalReviewer, PersistenceAgent, NotifierAgent.
"""
from core.utils import sast_str
from core.memory import load as load_memory
from agents.signal_reviewer import SignalReviewer, duplicate_review_guard
from agents.archive_agent import ArchiveAgent
from agents.persistence_agent import PersistenceAgent
from agents.notifier_agent import NotifierAgent


class IntelligenceAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def verdict(self, payload: dict) -> dict:
        # Keep duplicate review and archive commit in one per-symbol critical section.
        # This closes the in-process check/append race between concurrent scanner workers.
        with duplicate_review_guard(self.symbol):
            return self._verdict_under_duplicate_guard(payload)

    def _verdict_under_duplicate_guard(self, payload: dict) -> dict:
        print("[INTEL] " + self.symbol + ": starting")

        reviewer = SignalReviewer(self.symbol)

        if reviewer.is_duplicate(payload):
            if getattr(reviewer, "duplicate_check_status", None) == "unavailable":
                print("[INTEL] " + self.symbol + ": DUPLICATE REVIEW UNAVAILABLE - SIGNAL BLOCKED")
            else:
                print("[INTEL] " + self.symbol + ": DUPLICATE BLOCKED")
            return {}

        memory   = load_memory()
        accuracy = memory.get(self.symbol, {}).get("accuracy", "No history yet")

        review    = reviewer.review(payload, accuracy)
        narrative = reviewer.build_narrative(payload)

        trigger = payload["trigger"]
        result = {
            "id":              payload.get("id"),
            "symbol":          self.symbol,
            "timestamp":       sast_str(),
            "direction":       trigger.direction,
            "grade":           trigger.grade,
            "entry":           trigger.entry,
            "sl":              trigger.sl,
            "tp1":             trigger.tp1,
            "tp2":             trigger.tp2,
            "tp3":             trigger.tp3,
            "rr":              trigger.rr,
            "tags":            trigger.tags,
            "groq_verdict":    review["groq_verdict"],
            "skeptic_verdict": review["skeptic_verdict"],
            "regime":          payload.get("regime", {}),
            "score":           payload.get("score", {}),
            "edge":            payload.get("edge", {}),
        }

        # Persist the versioned decision record before any notification is sent.
        if not ArchiveAgent().log(payload):
            print("[INTEL] " + self.symbol + ": archive persistence failed; notification suppressed")
            return {}

        PersistenceAgent(self.symbol).save(result, payload)
        NotifierAgent(self.symbol).send(result, narrative)

        return result
