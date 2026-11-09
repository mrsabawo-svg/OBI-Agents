"""
OBI Agents - Factor Analytics Agent v4.3
Mines closed trade archive to compute win rates per factor, tag,
symbol, regime, grade and score bucket.
Stores results in memory so ChiefAgent and ScoreAgent can read them.
Run automatically after every lifecycle check.
"""
from core.memory import load as load_memory, save as save_memory

MIN_SAMPLE = 5  # minimum trades to report a meaningful win rate


class FactorAgent:
    def __init__(self):
        pass

    def analyse(self) -> dict:
        print("[FACTOR] Running Factor Analytics")
        try:
            memory  = load_memory() or {}
            archive = memory.get("_archive", [])
            closed  = [
                t for t in archive
                if t.get("status") == "CLOSED" and t.get("outcome") != "EXPIRED"
            ]

            if len(closed) < MIN_SAMPLE:
                print("[FACTOR] Insufficient data: " + str(len(closed)) + " closed trades")
                return {}

            report = {
                "sample_size":   len(closed),
                "overall_wr":    self._wr(closed),
                "by_factor":     self._by_factor(closed),
                "by_tag":        self._by_tag(closed),
                "by_symbol":     self._by_symbol(closed),
                "by_regime":     self._by_regime(closed),
                "by_grade":      self._by_grade(closed),
                "by_score":      self._by_score_bucket(closed),
                "top_factors":   self._top_factors(closed),
            }

            # Store in memory for other agents to read
            memory["_factor_analytics"] = report
            save_memory(memory)

            self._print_report(report)
            return report

        except Exception as e:
            print("[FACTOR] Error: " + str(e))
            return {}

    # ── Win rate helpers ───────────────────────────────────────────────────────

    def _wr(self, trades: list) -> float:
        if not trades:
            return 0.0
        wins = len([t for t in trades if t.get("outcome") in ["TP1", "TP2", "TP3"]])
        return round(wins / len(trades) * 100, 1)

    def _bucket(self, trades: list, key_fn) -> dict:
        groups = {}
        for t in trades:
            k = key_fn(t)
            if k not in groups:
                groups[k] = []
            groups[k].append(t)
        return {
            k: {"wr": self._wr(v), "n": len(v)}
            for k, v in groups.items()
            if len(v) >= MIN_SAMPLE
        }

    # ── Analysis methods ───────────────────────────────────────────────────────

    def _by_factor(self, closed: list) -> dict:
        all_factors = set()
        for t in closed:
            all_factors.update(t.get("factors", []))

        result = {}
        for factor in sorted(all_factors):
            subset = [t for t in closed if factor in t.get("factors", [])]
            if len(subset) >= MIN_SAMPLE:
                result[factor] = {"wr": self._wr(subset), "n": len(subset)}
        return result

    def _by_tag(self, closed: list) -> dict:
        all_tags = set()
        for t in closed:
            all_tags.update(t.get("tags", []))

        result = {}
        for tag in sorted(all_tags):
            subset = [t for t in closed if tag in t.get("tags", [])]
            if len(subset) >= MIN_SAMPLE:
                result[tag] = {"wr": self._wr(subset), "n": len(subset)}
        return result

    def _by_symbol(self, closed: list) -> dict:
        return self._bucket(closed, lambda t: t.get("symbol", "UNKNOWN"))

    def _by_regime(self, closed: list) -> dict:
        return self._bucket(closed, lambda t: t.get("regime", "UNKNOWN"))

    def _by_grade(self, closed: list) -> dict:
        return self._bucket(closed, lambda t: t.get("grade", "UNKNOWN"))

    def _by_score_bucket(self, closed: list) -> dict:
        def bucket_label(t):
            score = t.get("obi_score") or 0
            if score < 40:  return "0-39"
            if score < 55:  return "40-54"
            if score < 70:  return "55-69"
            return "70+"
        return self._bucket(closed, bucket_label)

    def _top_factors(self, closed: list) -> list:
        """Returns top 3 factors by win rate with MIN_SAMPLE trades."""
        all_factors = set()
        for t in closed:
            all_factors.update(t.get("factors", []))

        ranked = []
        for factor in all_factors:
            subset = [t for t in closed if factor in t.get("factors", [])]
            if len(subset) >= MIN_SAMPLE:
                ranked.append((factor, self._wr(subset), len(subset)))

        ranked.sort(key=lambda x: x[1], reverse=True)
        return [{"factor": f, "wr": w, "n": n} for f, w, n in ranked[:3]]

    # ── Reporting ──────────────────────────────────────────────────────────────

    def _print_report(self, report: dict) -> None:
        print("[FACTOR] ── Factor Analytics Report ──────────────────")
        print("[FACTOR] Sample: " + str(report["sample_size"]) + " trades | Overall WR: " + str(report["overall_wr"]) + "%")

        print("[FACTOR] By Factor:")
        for f, d in sorted(report["by_factor"].items(), key=lambda x: x[1]["wr"], reverse=True):
            print("[FACTOR]   " + f + ": " + str(d["wr"]) + "% (" + str(d["n"]) + " trades)")

        print("[FACTOR] By Tag:")
        for t, d in sorted(report["by_tag"].items(), key=lambda x: x[1]["wr"], reverse=True):
            print("[FACTOR]   " + t + ": " + str(d["wr"]) + "% (" + str(d["n"]) + " trades)")

        print("[FACTOR] By Symbol:")
        for s, d in sorted(report["by_symbol"].items(), key=lambda x: x[1]["wr"], reverse=True):
            print("[FACTOR]   " + s + ": " + str(d["wr"]) + "% (" + str(d["n"]) + " trades)")

        print("[FACTOR] By Regime:")
        for r, d in report["by_regime"].items():
            print("[FACTOR]   " + r + ": " + str(d["wr"]) + "% (" + str(d["n"]) + " trades)")

        print("[FACTOR] By Grade:")
        for g, d in sorted(report["by_grade"].items(), key=lambda x: x[1]["wr"], reverse=True):
            print("[FACTOR]   Grade " + g + ": " + str(d["wr"]) + "% (" + str(d["n"]) + " trades)")

        print("[FACTOR] By Score Bucket:")
        for b, d in sorted(report["by_score"].items()):
            print("[FACTOR]   Score " + b + ": " + str(d["wr"]) + "% (" + str(d["n"]) + " trades)")

        print("[FACTOR] Top 3 Factors:")
        for item in report["top_factors"]:
            print("[FACTOR]   " + item["factor"] + ": " + str(item["wr"]) + "% (" + str(item["n"]) + " trades)")
        print("[FACTOR] ─────────────────────────────────────────────")
