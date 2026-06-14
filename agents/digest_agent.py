"""
OBI Agents — Digest Agent
Generates and sends weekly performance digest via Gmail API.
Runs every Monday morning automatically.
"""
import os
import json
import requests
from datetime import datetime
import pytz
from core.memory import load as load_memory

SAST             = pytz.timezone("Africa/Johannesburg")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


class DigestAgent:
    def __init__(self):
        self.memory = load_memory() or {}

    def should_run(self) -> bool:
        now = datetime.now(SAST)
        return now.weekday() == 0 and now.hour == 8

    def generate(self) -> dict:
        archive  = self.memory.get("_archive", [])
        closed   = [t for t in archive if t.get("status") == "CLOSED"]
        open_t   = [t for t in archive if t.get("status") == "OPEN"]
        wins     = [t for t in closed if t.get("outcome") in ["TP1", "TP2", "TP3"]]
        losses   = [t for t in closed if t.get("outcome") == "SL"]
        expired  = [t for t in closed if t.get("outcome") == "EXPIRED"]
        total    = len(wins) + len(losses)
        win_rate = round((len(wins) / total) * 100, 1) if total > 0 else 0

        # Best performing symbol
        symbols = {}
        for t in closed:
            sym = t.get("symbol", "")
            if sym not in symbols:
                symbols[sym] = {"wins": 0, "total": 0}
            if t.get("outcome") in ["TP1", "TP2", "TP3"]:
                symbols[sym]["wins"] += 1
            symbols[sym]["total"] += 1

        best_symbol = None
        best_wr     = 0
        for sym, data in symbols.items():
            if data["total"] >= 3:
                wr = data["wins"] / data["total"]
                if wr > best_wr:
                    best_wr     = wr
                    best_symbol = sym

        return {
            "total_signals": len(archive),
            "open":          len(open_t),
            "closed":        len(closed),
            "wins":          len(wins),
            "losses":        len(losses),
            "expired":       len(expired),
            "win_rate":      win_rate,
            "best_symbol":   best_symbol,
            "best_wr":       round(best_wr * 100, 1),
            "week":          datetime.now(SAST).strftime("%Y-%m-%d"),
        }

    def send_telegram_digest(self):
        stats = self.generate()
        wr_display = str(stats["win_rate"]) + "%" if stats["wins"] + stats["losses"] > 0 else "Insufficient data"
        best = stats["best_symbol"] + " (" + str(stats["best_wr"]) + "% WR)" if stats["best_symbol"] else "Need more data"

        msg = (
            "OBI WEEKLY DIGEST\n"
            "Week of " + stats["week"] + "\n"
            "------------------------------\n"
            "Total Signals: " + str(stats["total_signals"]) + "\n"
            "Open: "          + str(stats["open"]) + "\n"
            "Wins: "          + str(stats["wins"]) + "\n"
            "Losses: "        + str(stats["losses"]) + "\n"
            "Expired: "       + str(stats["expired"]) + "\n"
            "Win Rate: "      + wr_display + "\n"
            "Best Symbol: "   + best + "\n"
            "------------------------------\n"
            "OBI Intelligence v4.2\n"
            "Keep building the edge."
        )
        try:
            requests.post(
                "https://api.telegram.org/bot" + str(TELEGRAM_TOKEN) + "/sendMessage",
                json={"chat_id": str(TELEGRAM_CHAT_ID), "text": msg},
                timeout=15
            )
            print("[DIGEST] Weekly digest sent to Telegram")
        except Exception as e:
            print("[DIGEST] Telegram error: " + str(e))
