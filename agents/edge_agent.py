"""
OBI Agents — Edge Agent
Learns from archived signal history.
Calculates win rates per symbol, grade, regime and factor.
Feeds intelligence back into signal scoring.
"""
from agents.archive_agent import ArchiveAgent


class EdgeAgent:
    def __init__(self):
        self.archive = ArchiveAgent()

    def analyse(self, symbol: str, grade: str, regime: str, factors: list) -> dict:
        print("[EDGE] Analysing edge for " + symbol)
        try:
            history  = self.archive.get_history(limit=500)
            closed   = [s for s in history if s.get("status") == "CLOSED"]

            if len(closed) < 5:
                print("[EDGE] Not enough history — defaulting")
                return self._default()

            symbol_wr  = self._win_rate(closed, "symbol", symbol)
            grade_wr   = self._win_rate(closed, "grade", grade)
            regime_wr  = self._win_rate(closed, "regime", regime)
            factor_wr  = self._factor_win_rate(closed, factors)
            overall_wr = self._win_rate(closed, None, None)

            edge_score = round(
                (symbol_wr * 0.35) +
                (grade_wr  * 0.25) +
                (regime_wr * 0.25) +
                (factor_wr * 0.15),
                2
            )

            print("[EDGE] " + symbol + ": edge=" + str(edge_score) +
                  " sym_wr=" + str(symbol_wr) +
                  " grade_wr=" + str(grade_wr) +
                  " regime_wr=" + str(regime_wr))

            return {
                "edge_score":  edge_score,
                "symbol_wr":   symbol_wr,
                "grade_wr":    grade_wr,
                "regime_wr":   regime_wr,
                "factor_wr":   factor_wr,
                "overall_wr":  overall_wr,
                "sample_size": len(closed)
            }

        except Exception as e:
            print("[EDGE] Error: " + str(e))
            return self._default()

    def _win_rate(self, signals: list, field: str, value) -> float:
        try:
            if field and value:
                filtered = [s for s in signals if s.get(field) == value]
            else:
                filtered = signals

            if not filtered:
                return 0.5

            wins = sum(1 for s in filtered if s.get("outcome") in ["TP1", "TP2", "TP3"])
            return round(wins / len(filtered), 2)
        except:
            return 0.5

    def _factor_win_rate(self, signals: list, factors: list) -> float:
        try:
            if not factors:
                return 0.5
            matched = [
                s for s in signals
                if any(f in s.get("bias_factors", []) for f in factors)
            ]
            if not matched:
                return 0.5
            wins = sum(1 for s in matched if s.get("outcome") in ["TP1", "TP2", "TP3"])
            return round(wins / len(matched), 2)
        except:
            return 0.5

    def _default(self) -> dict:
        return {
            "edge_score":  0.5,
            "symbol_wr":   0.5,
            "grade_wr":    0.5,
            "regime_wr":   0.5,
            "factor_wr":   0.5,
            "overall_wr":  0.5,
            "sample_size": 0
        }
