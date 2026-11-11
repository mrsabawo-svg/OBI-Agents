"""
OBI Agents — Edge Agent
Historical win-rate analytics over the shared eligible archive snapshot.
"""
from core.memory import load as load_memory
from core.archive_analytics_eligibility import select_eligible_events
from core.models import EdgeResult, TriggerResult

MIN_SAMPLE = 20


class EdgeAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.archive_snapshot_sha256 = None
        self.archive_eligibility_summary = {}

    def analyse(self, trigger: TriggerResult, bias, regime: dict) -> EdgeResult:
        print("[EDGE] Analysing edge for " + self.symbol)
        try:
            memory = load_memory() or {}
            selection = select_eligible_events(memory.get("_archive", []))
            closed = selection["events"]
            self.archive_snapshot_sha256 = selection["snapshot_sha256"]
            self.archive_eligibility_summary = {
                "eligible_count": len(closed),
                "duplicate_count": selection["duplicate_count"],
                "excluded_count": len(selection["excluded"]),
                "quarantined_count": len(selection["quarantined"]),
            }

            if len(closed) < MIN_SAMPLE:
                print("[EDGE] Low eligible terminal sample: " + str(len(closed)) + "/" + str(MIN_SAMPLE))
                return EdgeResult.default(len(closed))

            symbol_wr = self._win_rate([trade for trade in closed if trade.get("symbol") == self.symbol])
            regime_wr = self._win_rate([trade for trade in closed if trade.get("regime") == regime.get("label")])
            tag_wr = self._win_rate([
                trade for trade in closed
                if any(tag in trade.get("tags", []) for tag in trigger.tags)
            ])
            overall = self._win_rate(closed)

            # Pass 10 invariant: current trigger grade never influences historical edge.
            grade_wr = overall
            print(
                "[EDGE] " + self.symbol + ": sym=" + str(symbol_wr)
                + "% regime=" + str(regime_wr) + "% overall=" + str(overall) + "%"
            )
            return EdgeResult(
                symbol_wr=symbol_wr,
                grade_wr=grade_wr,
                regime_wr=regime_wr,
                tag_wr=tag_wr,
                overall_wr=overall,
                sample_size=len(closed),
                low_sample=False,
            )
        except Exception as exc:
            print("[EDGE] Error: " + str(exc))
            return EdgeResult.default(0)

    def _win_rate(self, signals: list) -> float:
        if not signals:
            return 50.0
        wins = sum(1 for signal in signals if signal.get("terminal_outcome") == "TP3")
        return round(wins / len(signals) * 100, 1)
