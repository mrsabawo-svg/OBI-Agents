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
            score_data = result.get("score")
            if hasattr(score_data, "grade"):
                grade = score_data.grade
                risk  = score_data.risk
            elif isinstance(score_data, dict):
                grade = score_data.get("grade", "N/A")
                risk  = score_data.get("risk", "N/A")
            else:
                grade = result.get("grade", "N/A")
                risk  = result.get("risk", "N/A")

            tags = " + ".join(result.get("tags", [])) or "none"
            confluence = result.get("confluence", "")
            conf_str = f" | Confluence: {confluence}" if confluence else ""

            msg = (
                f"OBI SIGNAL - {result['symbol']} [Grade: {grade} | Risk: {risk}]\n"
                "------------------------------\n"
                f"Direction: {result['direction']}{conf_str}\n"
                f"Entry: {round(float(result['entry']), 5)}\n"
                f"SL:    {result['sl']}\n"
                f"TP1:   {result['tp1']}\n"
                f"TP2:   {result['tp2']}\n"
                f"TP3:   {result['tp3']}\n"
                f"RR:    {result['rr']}\n"
                "------------------------------\n"
                + narrative + "\n"
                "------------------------------\n"
                + str(result.get("timestamp", ""))
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
