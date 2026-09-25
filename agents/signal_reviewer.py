"""
OBI Agents - Signal Reviewer v4.3
Handles: duplicate detection, Groq analysis, Skeptic second opinion, narrative.
Renamed: Devil's Advocate -> Skeptic (blunt, single purpose, no theatre)
"""
import os
import json
import hashlib
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

    @staticmethod
    def setup_identity(payload: dict) -> str:
        """Return a deterministic identity for the underlying trade setup.

        Signal identity answers: "Is this the exact signal event?"
        Setup identity answers: "Is this materially the same setup being
        proposed again?"  The two identities must not be conflated.
        """
        trigger = payload.get("trigger", {})
        get = lambda name, default=None: getattr(trigger, name, default) if hasattr(trigger, name) else trigger.get(name, default)
        setup = {
            "symbol": payload.get("symbol"),
            "direction": get("direction"),
            "entry": round(float(get("entry")), 5) if get("entry") is not None else None,
            "sl": round(float(get("sl")), 5) if get("sl") is not None else None,
            "tp1": round(float(get("tp1")), 5) if get("tp1") is not None else None,
            "tp2": round(float(get("tp2")), 5) if get("tp2") is not None else None,
            "tp3": round(float(get("tp3")), 5) if get("tp3") is not None else None,
            "tags": sorted(get("tags", []) or []),
            "regime": payload.get("regime", {}).get("label"),
        }
        encoded = json.dumps(setup, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]

    def is_duplicate(self, payload: dict) -> bool:
        try:
            memory = load_memory() or {}
            archive = memory.get("_archive", [])
            signal_id = payload.get("id")
            setup_id = self.setup_identity(payload)
            now = datetime.now(SAST)

            for t in archive:
                if t.get("status") != "OPEN" or t.get("symbol") != self.symbol:
                    continue

                # Exact signal identity is independent from setup identity.
                if signal_id and t.get("id") == signal_id:
                    return True

                try:
                    opened = datetime.strptime(
                        t.get("opened", "").replace(" SAST", ""),
                        "%Y-%m-%d %H:%M"
                    )
                    opened = SAST.localize(opened)
                    if (now - opened).total_seconds() >= 7200:
                        continue

                    archived_setup_id = t.get("setup_id")
                    if not archived_setup_id:
                        legacy_payload = {
                            "symbol": t.get("symbol"),
                            "trigger": {
                                "direction": t.get("direction"),
                                "entry": t.get("entry"),
                                "sl": t.get("sl"),
                                "tp1": t.get("tp1"),
                                "tp2": t.get("tp2"),
                                "tp3": t.get("tp3"),
                                "tags": t.get("tags", []),
                            },
                            "regime": {"label": t.get("regime")},
                        }
                        archived_setup_id = self.setup_identity(legacy_payload)

                    if archived_setup_id == setup_id:
                        return True
                except Exception:
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
            regime_label = payload.get("regime", {}).get("label", "Unknown")
            regime_conf  = payload.get("regime", {}).get("confidence", 0)
            trigger      = payload["trigger"]
            tags         = " + ".join(trigger.tags) or "none"
            bias_factors = payload["bias"].factors
            top_factors  = ", ".join(bias_factors[:3]) if bias_factors else "none"
            edge         = payload["edge"]
            score        = payload["score"]
            wr_line = (
                "LOW SAMPLE (" + str(edge.sample_size) + " trades)" if edge.low_sample
                else "Sym WR: " + str(edge.symbol_wr) + "% | Regime WR: " + str(edge.regime_wr) + "%"
            )
            return (
                "OBI Confidence: " + str(score.confidence) + "/100 | " +
                "Grade: " + str(score.grade) + " | " +
                "Risk: " + str(score.risk) + "\n" +
                "Edge: HMM " + regime_label + " (" + str(round(regime_conf * 100)) + "%) | " + tags + "\n" +
                wr_line + "\n" +
                "Factors: " + top_factors
            )
        except Exception as e:
            print("[REVIEWER] Narrative error: " + str(e))
            return "OBI Confidence: 50/100"

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
