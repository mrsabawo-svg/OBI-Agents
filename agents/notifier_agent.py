"""
OBI Agents - Notifier Agent
Handles: Telegram signal delivery only.
"""
import os
import requests

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


class NotifierAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def send(self, result: dict, narrative: str) -> None:
        print("[NOTIFY] sending Telegram for " + self.symbol)
        try:
            tags = " + ".join(result.get("tags", [])) or "none"
            gv   = str(result.get("groq_verdict", ""))[:400]
            sv   = str(result.get("skeptic_verdict", ""))[:400]
            msg = (
                "OBI SIGNAL - " + result["symbol"] + "\n"
                "------------------------------\n"
                + narrative + "\n"
                "------------------------------\n"
                "Direction: " + str(result["direction"]) + " | Tags: " + tags + "\n"
                "Entry: " + str(round(float(result["entry"]), 5)) + "\n"
                "SL: "    + str(result["sl"]) + "\n"
                "TP1: "   + str(result["tp1"]) + "\n"
                "TP2: "   + str(result["tp2"]) + "\n"
                "TP3: "   + str(result["tp3"]) + "\n"
                "RR: "    + str(result["rr"]) + "\n"
                "------------------------------\n"
                "ANALYST:\n" + gv + "\n"
                "------------------------------\n"
                "SKEPTIC:\n" + sv + "\n"
                "------------------------------\n"
                + str(result["timestamp"])
            )
            resp = requests.post(
                "https://api.telegram.org/bot" + str(TELEGRAM_TOKEN) + "/sendMessage",
                json={"chat_id": str(TELEGRAM_CHAT_ID), "text": msg},
                timeout=15
            )
            print("[NOTIFY] Telegram: " + str(resp.status_code))
            if resp.status_code != 200:
                print("[NOTIFY] Telegram error body: " + resp.text[:300])
        except Exception as e:
            print("[NOTIFY] Telegram error: " + str(e))
