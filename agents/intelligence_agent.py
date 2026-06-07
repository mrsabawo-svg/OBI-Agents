"""
OBI Agents — Intelligence Agent
"""
import os
import json
import requests
from core.utils import sast_str
from core.memory import load as load_memory, save as save_memory

GROQ_API_KEY     = os.environ.get("GROQ_API_KEY")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GIST_ID          = os.environ.get("GIST_ID")
GITHUB_TOKEN     = os.environ.get("GITHUB_TOKEN")

class IntelligenceAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def verdict(self, payload: dict) -> dict:
        print("[INTEL] " + self.symbol + ": starting")
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
        }
        self._update_memory(memory, result)
        self._push_to_gist(result)
        self._send_telegram(result)
        return result

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

    def _ask_devil(self, payload:
