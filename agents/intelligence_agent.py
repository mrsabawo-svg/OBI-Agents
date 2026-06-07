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
    def _ask_devil(self, payload: dict, groq_verdict: str) -> str:
        print("[INTEL] calling devil advocate")
        try:
            prompt = "You are a risk analyst. Another analyst said: " + groq_verdict + ". Play devil advocate. Format: SECOND OPINION / RISK SCORE 1-10 / RED FLAGS / ALTERNATIVE VIEW. Max 100 words. Signal: " + json.dumps(payload)
            return self._groq_call(prompt)
        except Exception as e:
            print("[INTEL] Devil error: " + str(e))
            return "Devil advocate unavailable"

    def _update_memory(self, memory: dict, result: dict):
        try:
            if self.symbol not in memory:
                memory[self.symbol] = {"signals": 0, "wins": 0, "losses": 0}
            memory[self.symbol]["signals"] = memory[self.symbol].get("signals", 0) + 1
            memory[self.symbol]["last_signal"]    = result.get("timestamp")
            memory[self.symbol]["last_direction"] = result.get("direction")
            memory[self.symbol]["last_signal_data"] = {
                "direction": result.get("direction"),
                "entry":     result.get("entry"),
                "sl":        result.get("sl"),
                "tp1":       result.get("tp1"),
                "tp2":       result.get("tp2"),
                "tp3":       result.get("tp3"),
                "timestamp": result.get("timestamp")
            }
            save_memory(memory)
            print("[INTEL] Memory updated")
        except Exception as e:
            print("[INTEL] Memory error: " + str(e))

    def _push_to_gist(self, result: dict):
        try:
            r = requests.patch(
                "https://api.github.com/gists/" + str(GIST_ID),
                headers={"Authorization": "token " + str(GITHUB_TOKEN)},
                json={"files": {"obi_signal.json": {"content": json.dumps(result, indent=2)}}},
                timeout=15
            )
            print("[INTEL] Gist: " + str(r.status_code))
        except Exception as e:
            print("[INTEL] Gist error: " + str(e))

    def _send_telegram(self, r: dict):
        print("[INTEL] sending Telegram")
        try:
            tags = " + ".join(r.get("tags", [])) or "none"
            gv   = str(r.get("groq_verdict", ""))[:400]
            dv   = str(r.get("devil_verdict", ""))[:400]
            msg  = (
                "OBI SIGNAL - " + r["symbol"] + "\n"
                "Grade: " + str(r["grade"]) + " | " + str(r["direction"]) + "\n"
                "Entry: " + str(round(float(r["entry"]), 5)) + "\n"
                "SL: " + str(r["sl"]) + "\n"
                "TP1: " + str(r["tp1"]) + "\n"
                "TP2: " + str(r["tp2"]) + "\n"
                "TP3: " + str(r["tp3"]) + "\n"
                "RR: " + str(r["rr"]) + " | Tags: " + tags + "\n"
                "------------------------------\n"
                "GROQ ANALYST:\n" + gv + "\n"
                "------------------------------\n"
                "DEVILS ADVOCATE:\n" + dv + "\n"
                "------------------------------\n"
                + str(r["timestamp"])
            )
            resp = requests.post(
                "https://api.telegram.org/bot" + str(TELEGRAM_TOKEN) + "/sendMessage",
                json={"chat_id": str(TELEGRAM_CHAT_ID), "text": msg},
                timeout=15
            )
            print("[INTEL] Telegram: " + str(resp.status_code))
        except Exception as e:
            print("[INTEL] Telegram error: " + str(e))
