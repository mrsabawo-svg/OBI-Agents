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
import pytz

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
SAST         = pytz.timezone("Africa/Johannesburg")


class SignalReviewer:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def is_duplicate(self, payload: dict) -> bool:
        try:
            memory    = load_memory()
            archive   = memory.get("_archive", [])
            new_entry = float(payload["trigger"].entry)
            now       = datetime.now(SAST)
            recent    = [t for t in archive if t.get("symbol") == self.symbol and t.get("status") == "OPEN"]
            for t in recent:
                try:
                    opened = datetime.strptime(t.get("opened", "").replace(" SAST", ""), "%Y-%m-%d %H:%M")
                    opened = SAST.localize(opened)
                    if (now - opened).total_seconds() < 7200:
                        if abs(float(t.get("entry", 0)) - new_entry) < 0.01:
                            return True
                except:
                    continue
            return False
        except Exception as e:
            print("[REVIEWER] Duplicate check error: " + str(e))
            return False

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
