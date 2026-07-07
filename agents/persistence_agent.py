"""
OBI Agents - Persistence Agent
Handles: memory updates, archive.
"""
from datetime import datetime
from core.memory import load as load_memory, save as save_memory


class PersistenceAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def save(self, result: dict, payload: dict) -> None:
        memory = load_memory()
        self._update_memory(memory, result, payload)

    def _update_memory(self, memory: dict, result: dict, payload: dict) -> None:
        try:
            if self.symbol not in memory:
                memory[self.symbol] = {"signals": 0, "wins": 0, "losses": 0}

            memory[self.symbol]["signals"]         = memory[self.symbol].get("signals", 0) + 1
            memory[self.symbol]["last_signal"]     = result.get("timestamp")
            memory[self.symbol]["last_direction"]  = result.get("direction")
            memory[self.symbol]["last_confidence"] = payload["score"].confidence

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

            # Full live snapshot — includes grade/rr/tags/regime/obi_score so an
            # open signal carries the same detail as a closed one in _archive.
            memory[self.symbol]["last_signal_data"] = {
                "id":        signal_id,
                "direction": result.get("direction"),
                "grade":     result.get("grade"),
                "entry":     result.get("entry"),
                "sl":        result.get("sl"),
                "tp1":       result.get("tp1"),
                "tp2":       result.get("tp2"),
                "tp3":       result.get("tp3"),
                "rr":        result.get("rr"),
                "tags":      result.get("tags", []),
                "regime":    trade_record["regime"],
                "obi_score": trade_record["obi_score"],
                "timestamp": result.get("timestamp")
            }

            save_memory(memory)
            print("[PERSIST] Memory updated - archived: " + signal_id)
        except Exception as e:
            print("[PERSIST] Memory error: " + str(e))
