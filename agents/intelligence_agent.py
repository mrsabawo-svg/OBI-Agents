"""
OBI Agents — Intelligence Agent
"""
import os
import json
import requests
from datetime import datetime, timedelta
from core.utils import sast_str
from core.memory import load as load_memory, save as save_memory
import pytz

GROQ_API_KEY     = os.environ.get("GROQ_API_KEY")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GIST_ID          = os.environ.get("GIST_ID")
GITHUB_TOKEN     = os.environ.get("GITHUB_TOKEN")
SAST             = pytz.timezone("Africa/Johannesburg")

class IntelligenceAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def verdict(self, payload: dict) -> dict:
        print("[INTEL] " + self.symbol + ": starting")

        if self._is_duplicate(payload):
            print("[INTEL] " + self.symbol + ": DUPLICATE BLOCKED")
            return {}

        memory   = load_memory()
        accuracy = memory.get(self.symbol, {}).get("accuracy", "No history yet")
        groq_verdict  = self._ask_groq(payload, accuracy)
        devil_verdict = self._ask_devil(payload, groq_verdict)
        result = {
            "symbol":        self.symbol,
            "timestamp":     sast_str(),
            "direction":     payload["trigger"].get("direction"),
            "grade":         payload["trigger"].get("grade"),
            "entry":         payload["trigger"].get("entry"),
            "sl":            payload["trigger"].get("sl"),
            "tp1":           payload["trigger"].get("tp1"),
            "tp2":           payload["trigger"].get("tp2"),
            "tp3":           payload["trigger"].get("tp3"),
            "rr":            payload["trigger"].get("rr"),
            "tags":          payload["trigger"].get("tags", []),
            "groq_verdict":  groq_verdict,
            "devil_verdict": devil_verdict,
            "regime":        payload.get("regime", {}),
            "score":         payload.get("score", {}),
            "edge":          payload.get("edge", {}),
        }
        self._update_memory(memory, result, payload)
        self._push_to_gist(result)
        self._send_telegram(result, payload)
        return result

    def _is_duplicate(self, payload: dict) -> bool:
        try:
            memory    = load_memory()
            archive   = memory.get("_archive", [])
            new_entry = float(payload.get("trigger", {}).get("entry", 0))
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
            print("[INTEL] Duplicate check error: " + str(e))
            return False

    def _groq_call(self, prompt: str) -> str:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": "Bearer " + str(GROQ_API_KEY), "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "max_tokens": 250},
            timeout=30
        )
        print("[INTEL] Groq status: " + str(r.status_code))
        return r.json()["choices"][0]["message"]["content"].strip()

    def _ask_groq(self, payload: dict, accuracy: str) -> str:
        print("[INTEL] calling Groq analyst")
        try:
            prompt = "You are a professional trading analyst. Review this signal and respond with: 1. VERDICT: TAKE IT / LEAVE IT / WAIT 2. CONFIDENCE: 1-10 3. STRENGTHS 4. CONCERNS 5. WATCH. Max 150 words. Historical accuracy: " + str(accuracy) + " Signal: " + json.dumps(payload)
            return self._groq_call(prompt)
        except Exception as e:
            print("[INTEL] Groq error: " + str(e))
            return "Groq unavailable"

    def _ask_devil(self, payload: dict, groq_verdict: str) -> str:
        print("[INTEL] calling devil advocate")
        try:
            prompt = "You are a risk analyst. Another analyst said: " + groq_verdict + ". Play devil advocate. Format: SECOND OPINION / RISK SCORE 1-10 / RED FLAGS / ALTERNATIVE VIEW. Max 100 words. Signal: " + json.dumps(payload)
            return self._groq_call(prompt)
        except Exception as e:
            print("[INTEL] Devil error: " + str(e))
            return "Devil advocate unavailable"
