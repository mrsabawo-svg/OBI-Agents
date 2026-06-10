"""
OBI Agents — Edge Agent
Historical win rate analytics.
"""
from core.memory import load as load_memory

MIN_SAMPLE = 20

class EdgeAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def analyse(self, trigger: dict, bias: dict, regime: dict) -> dict:
        print("[EDGE] Analysing edge for " + self.symbol)
        try:
            memory  = load_memory()
            archive = memory.get("_archive", [])
            closed  = [t for t in archive if t.get("status") == "CLOSED" and t.get("outcome") != "EXPIRED"]

            if len(closed) < MIN_SAMPLE:
                print("[EDGE] Low sample: " + str(len(closed)) + "/" + str(MIN_SAMPLE))
                return self._default(len(closed))

            symbol_wr = self._win_rate([t for t in closed if t.get("symbol") == self.symbol])
            grade_wr  = self._win_rate([t for t in closed if t.get("grade") == trigger.get("grade")])
            regime_wr = self._win_rate([t for t in closed if t.get("regime") == regime.get("label")])
            tags      = trigger.get("tags", [])
            tag_wr    = self._win_rate([t for t in closed if any(tag in t.get("tags", []) for tag in tags)])
            overall   = self._win_rate(closed)

            print("[EDGE] " + self.symbol + ": sym=" + str(symbol_wr) + "% grade=" + str(grade_wr) + "% regime=" + str(regime_wr) + "%")

            return {
                "symbol_wr":   symbol_wr,
                "grade_wr":    grade_wr,
                "regime_wr":   regime_wr,
                "tag_wr":      tag_wr,
                "overall_wr":  overall,
                "sample_size": len(closed),
                "low_sample":  len(closed) < MIN_SAMPLE
            }

        except Exception as e:
            print("[EDGE] Error: " + str(e))
            return self._default(0)

    def _win_rate(self, signals: list) -> float:
        if not signals:
            return 50.0
        wins = len([s for s in signals if s.get("outcome") in ["TP1", "TP2", "TP3"]])
        return round((wins / len(signals)) * 100, 1)

    def _default(self, sample_size: int) -> dict:
        return {
            "symbol_wr":   50.0,
            "grade_wr":    50.0,
            "regime_wr":   50.0,
            "tag_wr":      50.0,
            "overall_wr":  50.0,
            "sample_size": sample_size,
            "low_sample":  True
        }
