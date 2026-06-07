"""
OBI Agents — Health Monitor Agent
Watches system health and alerts via Telegram if something breaks.
"""
import os
import requests
from datetime import datetime, timedelta
import pytz
from core.memory import load as load_memory, save as save_memory

SAST             = pytz.timezone("Africa/Johannesburg")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Thresholds
MAX_SIGNAL_SILENCE_HOURS = 8
MAX_RUN_SILENCE_HOURS    = 1


class HealthAgent:
    def __init__(self):
        self.issues = []

    def check(self, run_results: dict) -> dict:
        print("[HEALTH] Running system health check")
        self.issues = []

        self._check_agent_failures(run_results)
        self._check_data_health(run_results)
        self._check_signal_silence()
        self._check_memory()

        if self.issues:
            self._send_alert()
            return {"healthy": False, "issues": self.issues}

        print("[HEALTH] All systems OK")
        self._update_last_healthy()
        return {"healthy": True, "issues": []}

    def _check_agent_failures(self, run_results: dict):
        for symbol, result in run_results.items():
            if result.get("error"):
                self.issues.append("Agent error on " + symbol + ": " + str(result["error"]))

    def _check_data_health(self, run_results: dict):
        empty_count = sum(1 for r in run_results.values() if r.get("data_empty"))
        if empty_count >= 3:
            self.issues.append("Data fetch failing on " + str(empty_count) + " symbols")

    def _check_signal_silence(self):
        try:
            memory = load_memory()
            now    = datetime.now(SAST)
            last_signal_times = []

            for symbol, data in memory.items():
                last = data.get("last_signal")
                if last:
                    try:
                        dt = datetime.fromisoformat(last.replace(" SAST", ""))
                        dt = SAST.localize(dt)
                        last_signal_times.append(dt)
                    except:
                        continue

            if not last_signal_times:
                return

            most_recent = max(last_signal_times)
            hours_since = (now - most_recent).total_seconds() / 3600

            if hours_since > MAX_SIGNAL_SILENCE_HOURS:
                self.issues.append(
                    "No signals fired in " + str(round(hours_since, 1)) + " hours. Last signal: " + most_recent.strftime("%Y-%m-%d %H:%M SAST")
                )
        except Exception as e:
            print("[HEALTH] Signal silence check error: " + str(e))

    def _check_memory(self):
        try:
            memory = load_memory()
            if not memory:
                self.issues.append("Memory Gist is empty or unreachable")
        except Exception as e:
            self.issues.append("Memory check failed: " + str(e))

    def _update_last_healthy(self):
        try:
            memory = load_memory()
            memory["_health"] = {
                "last_healthy": datetime.now(SAST).strftime("%Y-%m-%d %H:%M SAST"),
                "status": "OK"
            }
            save_memory(memory)
        except Exception as e:
            print("[HEALTH] Update error: " + str(e))

    def _send_alert(self):
        try:
            issues_text = "\n".join(["- " + i for i in self.issues])
            msg = (
                "OBI HEALTH ALERT\n"
                "------------------------------\n"
                + issues_text + "\n"
                "------------------------------\n"
                "Time: " + datetime.now(SAST).strftime("%Y-%m-%d %H:%M SAST") + "\n"
                "Action: Check GitHub Actions logs"
            )
            requests.post(
                "https://api.telegram.org/bot" + str(TELEGRAM_TOKEN) + "/sendMessage",
                json={"chat_id": str(TELEGRAM_CHAT_ID), "text": msg},
                timeout=15
            )
            print("[HEALTH] Alert sent to Telegram")
        except Exception as e:
            print("[HEALTH] Alert send error: " + str(e))
