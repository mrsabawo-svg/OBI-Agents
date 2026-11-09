"""
OBI Agents - Signal Reviewer v4.3
Handles: duplicate detection, Groq analysis, Skeptic second opinion, narrative.
Renamed: Devil's Advocate -> Skeptic (blunt, single purpose, no theatre)
"""
import os
import json
import requests
from datetime import datetime
from core.memory import load as load_memory
from core.models import _payload_to_dict
from core.signal_identity import SETUP_IDENTITY_VERSION, build_setup_identity
import pytz

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
SAST         = pytz.timezone("Africa/Johannesburg")


class SignalReviewer:
    def __init__(self, symbol: str):
        self.symbol = symbol

    @staticmethod
    def setup_identity(payload: dict) -> str:
        """Return the versioned deterministic identity of a material trade setup."""
        return build_setup_identity(payload)

    def is_duplicate(self, payload: dict) -> bool:
        try:
            memory = load_memory() or {}
            archive = memory.get("_archive", [])
            signal_id = payload.get("id")
            setup_id = self.setup_identity(payload)
            now = datetime.now(SAST)

            for archived in archive:
                if not isinstance(archived, dict) or archived.get("symbol") != self.symbol:
                    continue

                # Exact event identity is permanent, including after closure.
                if signal_id and archived.get("id") == signal_id:
                    return True

                # Setup-level suppression applies only to currently OPEN records.
                if archived.get("status") != "OPEN":
                    continue

                opened_value = archived.get("opened") or archived.get("timestamp")
                opened = self._parse_opened_at(opened_value)
                if opened is None:
                    continue
                age_seconds = (now - opened).total_seconds()
                if not 0 <= age_seconds < 7200:
                    continue

                archived_setup_id = archived.get("setup_id")
                archived_setup_version = archived.get("setup_identity_version")
                if (
                    archived_setup_version != SETUP_IDENTITY_VERSION
                    or not isinstance(archived_setup_id, str)
                    or not archived_setup_id
                ):
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
                    try:
                        archived_setup_id = self.setup_identity(legacy_payload)
                    except (TypeError, ValueError):
                        continue

                if archived_setup_id == setup_id:
                    return True

            return False
        except Exception as e:
            print("[REVIEWER] Duplicate check error: " + str(e))
            return False

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
