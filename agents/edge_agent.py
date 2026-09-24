"""
OBI Agents — Edge Agent
Historical edge evidence.

Pass 10 invariant:
- Edge lookup MUST NOT use the current signal's categorical Trigger grade.
- Historical evidence is a prior, not a veto.
- Edge supplies evidence to Score; it has no independent approve/reject authority.
- Segment lookups require MIN_SAMPLE; otherwise the agent falls back
  hierarchically to broader independent feature sets and finally the
  overall base rate.
"""
from math import sqrt

from core.memory import load as load_memory
from core.models import EdgeResult, TriggerResult

MIN_SAMPLE = 20
WIN_OUTCOMES = {"TP1", "TP2", "TP3"}


class EdgeAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def analyse(self, trigger: TriggerResult, bias, regime: dict) -> EdgeResult:
        print("[EDGE] Analysing independent historical edge for " + self.symbol)
        try:
            memory = load_memory() or {}
            archive = memory.get("_archive", [])
            closed = [
                t for t in archive
                if t.get("status") == "CLOSED"
                and t.get("outcome") not in {"EXPIRED", None, "PENDING"}
            ]

            if len(closed) < MIN_SAMPLE:
                print("[EDGE] Low sample: " + str(len(closed)) + "/" + str(MIN_SAMPLE))
                return EdgeResult.default(len(closed))

            regime_label = regime.get("label")
            trigger_tags = set(trigger.tags or [])

            symbol_rows = [t for t in closed if t.get("symbol") == self.symbol]
            regime_rows = [t for t in symbol_rows if t.get("regime") == regime_label]
            tag_rows = [
                t for t in regime_rows
                if trigger_tags and trigger_tags.intersection(set(t.get("tags", [])))
            ]

            # Grade is deliberately excluded from every lookup key.
            candidates = [
                ("SYMBOL+REGIME+TAG", tag_rows),
                ("SYMBOL+REGIME", regime_rows),
                ("SYMBOL", symbol_rows),
                ("BASE_RATE", closed),
            ]

            selected_level, selected_rows = next(
                ((level, rows) for level, rows in candidates if len(rows) >= MIN_SAMPLE),
                ("BASE_RATE", closed),
            )

            wr = self._win_rate(selected_rows)
            interval = self._wilson_interval(selected_rows)
            symbol_wr = self._win_rate(symbol_rows)
            regime_wr = self._win_rate(regime_rows)
            tag_wr = self._win_rate(tag_rows)
            overall_wr = self._win_rate(closed)

            print(
                "[EDGE] " + self.symbol
                + ": level=" + selected_level
                + " wr=" + str(wr)
                + "% n=" + str(len(selected_rows))
                + " CI=" + str(interval)
            )

            return EdgeResult(
                symbol_wr=symbol_wr,
                selected_wr=wr,
                regime_wr=regime_wr,
                tag_wr=tag_wr,
                overall_wr=overall_wr,
                sample_size=len(selected_rows),
                low_sample=False,
                confidence_interval=interval,
                evidence_level="E3",
                lookup_level=selected_level,
            )

        except Exception as e:
            print("[EDGE] Error: " + str(e))
            return EdgeResult.default(0)

    def _win_rate(self, signals: list) -> float:
        if not signals:
            return 50.0
        wins = sum(1 for s in signals if s.get("outcome") in WIN_OUTCOMES)
        return round((wins / len(signals)) * 100, 1)

    def _wilson_interval(self, signals: list) -> tuple:
        n = len(signals)
        if n == 0:
            return (0.0, 100.0)
        wins = sum(1 for s in signals if s.get("outcome") in WIN_OUTCOMES)
        p = wins / n
        z = 1.96
        denominator = 1 + (z * z / n)
        centre = p + (z * z / (2 * n))
        spread = z * sqrt((p * (1 - p) / n) + (z * z / (4 * n * n)))
        low = max(0.0, (centre - spread) / denominator) * 100
        high = min(1.0, (centre + spread) / denominator) * 100
        return (round(low, 1), round(high, 1))
