"""
OBI Agents - Persistence Agent
Handles: memory updates, archive, gist push.
"""
import os
import json
import requests
from datetime import datetime
from core.memory import load as load_memory, save as save_memory

GIST_ID      = os.environ.get("GIST_ID")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")


class PersistenceAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def save(self, result: dict, payload: dict) -> None:
        memory = load_memory()
        self._update_memory(memory, result, payload)
        self._push_to_gist(result)

    def _update_memory(self, memory: dict, result: dict, payload: dict) -> None:
        try:
            if self.symbol not in memory:
                memory[self.symbol] = {"signals": 0, "wins": 0, "losses": 0}

            memory[self.symbol]["signals"]        = memory[self.symbol].get("signals", 0) + 1
            memory[self.symbol]["last_signal"]    = result.get("timestamp")
            memory[self.symbol]["last_direction"] = result.get("direction")

            signal_id    = self.symbol + "_" + datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            trade_record = {
                "id":         signal_id,
                "symbol":     self.symbol,
                "status":     "OPEN",
                "direction":  result.get("direction"),
                "grade":      result.get("grade"),
                "entry":      result.get("entry"),
                "sl":         result.get("sl"),
                "tp1":        result.get("tp1"),
                "tp2":        result.get("tp2"),
                "tp3":        result.get("tp3"),
                "rr":         result.get("rr"),
                "tags":       result.get("tags", []),
                "factors":    payload["bias"].factors,
                "bias_grade": payload["bias"].grade,
                "obi_score":  payload["score"].confidence,
                "regime":     payload.get("regime", {}).get("label"),
                "opened":     result.get("timestamp"),
                "closed":     None,
                "tp1_hit":    False,
                "tp2_hit":    False,
                "tp3_hit":    False,
                "outcome":    None
            }

            if "_archive" not in memory:
                memory["_archive"] = []
            memory["_archive"].append(trade_record)

            memory[self.symbol]["last_signal_data"] = {
                "id":        signal_id,
                "direction": result.get("direction"),
                "entry":     result.get("entry"),
                "sl":        result.get("sl"),
                "tp1":       result.get("tp1"),
                "tp2":       result.get("tp2"),
                "tp3":       result.get("tp3"),
                "timestamp": result.get("timestamp")
            }

            save_memory(memory)
            print("[PERSIST] Memory updated - archived: " + signal_id)
        except Exception as e:
            print("[PERSIST] Memory error: " + str(e))

    def _push_to_gist(self, result: dict) -> None:
        try:
            safe_result = {}
            for k, v in result.items():
                if hasattr(v, "__dataclass_fields__"):
                    safe_result[k] = v.__dict__
                else:
                    safe_result[k] = v
            r = requests.patch(
                "https://api.github.com/gists/" + str(GIST_ID),
                headers={"Authorization": "token " + str(GITHUB_TOKEN)},
                json={"files": {"obi_signal.json": {"content": json.dumps(safe_result, indent=2)}}},
                timeout=15
            )
            print("[PERSIST] Gist: " + str(r.status_code))
            if r.status_code != 200:
                print("[PERSIST] Gist error body: " + r.text[:300])
        except Exception as e:
            print("[PERSIST] Gist error: " + str(e))
