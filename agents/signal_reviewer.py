"""
OBI Agents - Signal Reviewer v4.3
Handles: duplicate detection, Groq analysis, Skeptic second opinion, narrative.
Renamed: Devil's Advocate -> Skeptic (blunt, single purpose, no theatre)
"""
import os
import json
import requests
import threading
from contextlib import contextmanager
from datetime import datetime
import core.memory as memory_store
from core.models import _payload_to_dict
from core.signal_identity import SETUP_IDENTITY_VERSION, resolve_setup_identity
import pytz

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
SAST         = pytz.timezone("Africa/Johannesburg")

_REVIEW_LOCKS = {}
_REVIEW_LOCKS_GUARD = threading.Lock()


@contextmanager
def duplicate_review_guard(symbol: str):
    """Serialize duplicate-check through archive commit within this process."""
    key = str(symbol).strip().upper()
    with _REVIEW_LOCKS_GUARD:
        lock = _REVIEW_LOCKS.setdefault(key, threading.RLock())
    with lock:
        yield


class DuplicateReviewUnavailable(RuntimeError):
    """Duplicate status cannot be established from trustworthy archive state."""


class SignalReviewer:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.duplicate_check_status = "not_checked"

    @staticmethod
    def setup_identity(payload: dict) -> str:
        """Return the versioned deterministic identity of a material trade setup."""
        return resolve_setup_identity(payload)

    def is_duplicate(self, payload: dict) -> bool:
        """Return True for duplicates OR when safe duplicate status is unavailable."""
        try:
            if not isinstance(payload, dict):
                raise DuplicateReviewUnavailable("candidate payload is not an object")
            if payload.get("symbol") != self.symbol:
                raise DuplicateReviewUnavailable("candidate instrument does not match reviewer")
            signal_id = payload.get("id")
            if not isinstance(signal_id, str) or not signal_id.strip():
                raise DuplicateReviewUnavailable("candidate event identity is missing")
            setup_id = self.setup_identity(payload)

            memory, is_stale = memory_store.load_with_status()
            if not isinstance(memory, dict):
                raise DuplicateReviewUnavailable("canonical memory root is not an object")
            if is_stale:
                raise DuplicateReviewUnavailable("canonical memory is stale/read-only")
            archive = memory.get("_archive", [])
            if not isinstance(archive, list):
                raise DuplicateReviewUnavailable("archive is not a list")

            now = datetime.now(SAST)
            for archived in archive:
                if not isinstance(archived, dict):
                    raise DuplicateReviewUnavailable("archive contains a malformed record")
                archived_symbol = archived.get("symbol")
                if not isinstance(archived_symbol, str) or not archived_symbol.strip():
                    raise DuplicateReviewUnavailable("archive record has no valid instrument")
                if archived_symbol != self.symbol:
                    continue

                archived_id = archived.get("id")
                if archived_id is not None and (
                    not isinstance(archived_id, str) or not archived_id.strip()
                ):
                    raise DuplicateReviewUnavailable("archive record has malformed event identity")
                if archived_id == signal_id:
                    self.duplicate_check_status = "duplicate"
                    return True

                status = archived.get("status")
                if not isinstance(status, str):
                    raise DuplicateReviewUnavailable("archive record has malformed lifecycle status")
                if status != "OPEN":
                    continue

                opened_value = archived.get("opened") or archived.get("timestamp")
                opened = self._parse_opened_at(opened_value)
                if opened is None:
                    raise DuplicateReviewUnavailable("open archive record has invalid timestamp")
                age_seconds = (now - opened).total_seconds()
                if age_seconds < 0:
                    raise DuplicateReviewUnavailable("open archive record has a future timestamp")
                if age_seconds >= 7200:
                    continue

                archived_setup_id = archived.get("setup_id")
                archived_setup_version = archived.get("setup_identity_version")
                legacy_payload = {
                    "symbol": archived.get("symbol"),
                    "trigger": {
                        "direction": archived.get("direction"),
                        "entry": archived.get("entry"),
                        "sl": archived.get("sl"),
                        "tp1": archived.get("tp1"),
                        "tp2": archived.get("tp2"),
                        "tp3": archived.get("tp3"),
                        "tags": archived.get("tags", []),
                    },
                    "regime": {"label": archived.get("regime")},
                }

                if archived_setup_version == SETUP_IDENTITY_VERSION:
                    if (
                        not isinstance(archived_setup_id, str)
                        or len(archived_setup_id) != 64
                        or any(ch not in "0123456789abcdef" for ch in archived_setup_id)
                    ):
                        raise DuplicateReviewUnavailable("versioned archive setup identity is malformed")
                    reconstructed = self.setup_identity(legacy_payload)
                    if reconstructed != archived_setup_id:
                        raise DuplicateReviewUnavailable("archive setup identity conflicts with stored material")
                elif archived_setup_version is None and archived_setup_id is None:
                    # Legacy records may be reconstructed only when all material fields exist.
                    try:
                        archived_setup_id = self.setup_identity(legacy_payload)
                    except (TypeError, ValueError) as exc:
                        raise DuplicateReviewUnavailable("legacy archive setup cannot be reconstructed") from exc
                else:
                    raise DuplicateReviewUnavailable("unsupported or conflicting archive identity version")

                if archived_setup_id == setup_id:
                    self.duplicate_check_status = "duplicate"
                    return True

            self.duplicate_check_status = "clear"
            return False
        except Exception as e:
            self.duplicate_check_status = "unavailable"
            print("[REVIEWER] Duplicate check unavailable; blocking signal: " + str(e))
            return True

    @staticmethod
    def _parse_opened_at(value):
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and value.strip():
            label = value.strip().replace(" SAST", "")
            parsed = None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    parsed = datetime.strptime(label, fmt)
                    break
                except ValueError:
                    pass
            if parsed is None:
                try:
                    parsed = datetime.fromisoformat(label.replace("Z", "+00:00"))
                except ValueError:
                    return None
        else:
            return None

        if parsed.tzinfo is None:
            return SAST.localize(parsed)
        return parsed.astimezone(SAST)

    def review(self, payload: dict, accuracy: str) -> dict:
        groq_verdict     = self._ask_groq(payload, accuracy)
        skeptic_verdict  = self._ask_skeptic(payload, groq_verdict)
        return {
            "groq_verdict":    groq_verdict,
            "skeptic_verdict": skeptic_verdict,
            "narrative":       self.build_narrative(payload),
        }

    def build_narrative(self, payload: dict) -> str:
        try:
            regime_dict  = payload.get("regime", {})
            regime_label = regime_dict.get("label", "RANGING")
            trigger      = payload.get("trigger")
            tags         = " + ".join(getattr(trigger, "tags", []) if trigger else []) or "Structure"
            session_info = payload.get("session", {})
            session_name = "Kill Zone" if session_info.get("kill_zone") else "Standard Session"

            # Setup summary: concise confluence of market context
            setup_line = f"SETUP: {session_name} + {regime_label} Regime ({tags})"

            # Trap / Invalidation watch: concise objective risk warning
            score_obj = payload.get("score")
            risk_label = getattr(score_obj, "risk", "MEDIUM") if score_obj else "MEDIUM"
            edge_obj   = payload.get("edge")

            if regime_label == "VOLATILE":
                trap_note = "Elevated volatility; enforce strict SL and avoid early re-entry."
            elif edge_obj and getattr(edge_obj, "low_sample", False):
                trap_note = "Low sample size; monitor price action closely at TP1."
            elif risk_label == "HIGH":
                trap_note = "Lower reward-risk ratio; watch for liquidity sweeps against position."
            else:
                trap_note = "Invalidated on close beyond SL; protect capital at TP1."

            watch_line = f"TRAP / WATCH: {trap_note}"

            return f"{setup_line}\n{watch_line}"
        except Exception as e:
            print("[REVIEWER] Narrative error: " + str(e))
            return "SETUP: Active Signal\nTRAP / WATCH: Monitor position strictly at SL."

    def _groq_call(self, prompt: str) -> str:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": "Bearer " + str(GROQ_API_KEY), "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "max_tokens": 250},
            timeout=30
        )
        print("[REVIEWER] Groq status: " + str(r.status_code))
        return r.json()["choices"][0]["message"]["content"].strip()

    def _ask_groq(self, payload: dict, accuracy: str) -> str:
        print("[REVIEWER] calling Groq analyst")
        try:
            from agents.exa_agent import ExaAgent
            market_context = ExaAgent(self.symbol).get_context()
            safe_payload   = _payload_to_dict(payload)
            prompt = (
                "You are a professional forex and crypto trading analyst. "
                "Current market context: " + market_context + " "
                "Review this signal and respond with: "
                "1. VERDICT: TAKE IT / LEAVE IT / WAIT "
                "2. CONFIDENCE: 1-10 "
                "3. STRENGTHS "
                "4. CONCERNS "
                "5. WATCH. "
                "Max 150 words. "
                "Historical accuracy: " + str(accuracy) + " "
                "Signal: " + json.dumps(safe_payload)
            )
            return self._groq_call(prompt)
        except Exception as e:
            print("[REVIEWER] Groq error: " + str(e))
            return "Groq unavailable"

    def _ask_skeptic(self, payload: dict, groq_verdict: str) -> str:
        print("[REVIEWER] calling Skeptic")
        try:
            safe_payload = _payload_to_dict(payload)
            prompt = (
                "You are a blunt risk analyst. No fluff. Another analyst said: " + groq_verdict +
                ". Punch holes in it. Format: "
                "VERDICT: AGREE / DISAGREE / PARTIAL | "
                "RISK: 1-10 | "
                "RED FLAGS: (list only real concerns, not generic ones) | "
                "BOTTOM LINE: one sentence. "
                "Max 80 words. Signal: " + json.dumps(safe_payload)
            )
            return self._groq_call(prompt)
        except Exception as e:
            print("[REVIEWER] Skeptic error: " + str(e))
            return "Skeptic unavailable"
