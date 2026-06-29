"""
OBI Agents — Edge Agent
Historical win rate analytics.
"""
from core.memory import load as load_memory
from core.models import EdgeResult, TriggerResult

MIN_SAMPLE = 20

class EdgeAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def analyse(self, trigger: TriggerResult, bias, regime: dict) -> EdgeResult:
        print("[EDGE] Analysing edge for " + self.symbol)
        try:
            memory  = load_memory()
            archive = memory.get("_archive", [])
            closed  = [t for t in archive if t.get("status") == "CLOSED" and t.get("outcome") != "EXPIRED"]

            if len(closed) < MIN_SAMPLE:
                print("[EDGE] Low sample: " + str(len(closed)) + "/" + str(MIN_SAMPLE))
                return EdgeResult.default(len(closed))

            symbol_wr = self._win_rate([t for t in closed if t.get("symbol") == self.symbol])
            grade_wr  = self._win_rate([t for t in closed if t.get("grade") == trigger.grade])
            regime_wr = self._win_rate([t for t in closed if t.get("regime") == regime.get("label")])
            tag_wr    = self._win_rate([t for t in closed if any(tag in t.get("tags", []) for tag in trigger.tags)])
            overall   = self._win_rate(closed)

            print("[EDGE] " + self.symbol + ": sym=" + str(symbol_wr) + "% grade=" + str(grade_wr) + "% regime=" + str(regime_wr) + "%")

            return EdgeResult(
                symbol_wr=symbol_wr,
                grade_wr=grade_wr,
                regime_wr=regime_wr,
                tag_wr=tag_wr,
                overall_wr=overall,
                sample_size=len(closed),
                low_sample=False
            )

        except Exception as e:
            print("[EDGE] Error: " + str(e))
            return EdgeResult.default(0)

    def _win_rate(self, signals: list) -> float:
        if not signals:
            return 50.0
        wins = len([s for s in signals if s.get("outcome") in ["TP1", "TP2", "TP3"]])
        return round((wins / len(signals)) * 100, 1)
