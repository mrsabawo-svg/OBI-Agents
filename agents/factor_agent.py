"""
OBI Agents - Factor Analytics Agent v4.3
Mines eligible terminal archive events to compute win rates by factor, tag,
symbol, regime, versioned grade and score bucket.
"""
from core.memory import load as load_memory, save as save_memory
from core.archive_analytics_eligibility import select_eligible_events

MIN_SAMPLE = 5  # minimum trades to report a meaningful win rate


class FactorAgent:
    def __init__(self):
        pass

    def analyse(self) -> dict:
        print("[FACTOR] Running Factor Analytics")
        try:
            memory = load_memory() or {}
            archive = memory.get("_archive", [])
            selection = select_eligible_events(archive)
            closed = selection["events"]
            memory["_factor_analytics_eligibility"] = {
                "schema_version": "obi-archive-analytics-eligibility-v1",
                "eligible_count": len(closed),
                "duplicate_count": selection["duplicate_count"],
                "excluded_count": len(selection["excluded"]),
                "quarantined_count": len(selection["quarantined"]),
                "snapshot_sha256": selection["snapshot_sha256"],
            }

            if len(closed) < MIN_SAMPLE:
                # Never leave a previous report visible after the trusted
                # eligible sample falls below the reporting threshold.
                memory["_factor_analytics"] = {}
                print(
                    "[FACTOR] Insufficient eligible terminal data: "
                    + str(len(closed)) + " / " + str(MIN_SAMPLE)
                )
                if save_memory(memory) is not True:
                    print("[FACTOR] Eligibility diagnostics were not persisted")
                return {}

            report = {
                "eligibility_schema_version": "obi-archive-analytics-eligibility-v1",
                "archive_snapshot_sha256": selection["snapshot_sha256"],
                "sample_size": len(closed),
                "overall_wr": self._wr(closed),
                "by_factor": self._by_factor(closed),
                "by_tag": self._by_tag(closed),
                "by_symbol": self._by_symbol(closed),
                "by_regime": self._by_regime(closed),
                "by_grade": self._by_grade(closed),
                "by_score": self._by_score_bucket(closed),
                "top_factors": self._top_factors(closed),
            }

            memory["_factor_analytics"] = report
            if save_memory(memory) is not True:
                print("[FACTOR] Analytics report computed but not persisted")

            self._print_report(report)
            return report

        except Exception as e:
            print("[FACTOR] Error: " + str(e))
            return {}

    # Win-rate helpers

    def _wr(self, trades: list) -> float:
        if not trades:
            return 0.0
        wins = sum(1 for trade in trades if trade.get("terminal_outcome") == "TP3")
        return round(wins / len(trades) * 100, 1)

    def _bucket(self, trades: list, key_fn) -> dict:
        groups = {}
        for trade in trades:
            key = key_fn(trade)
            groups.setdefault(key, []).append(trade)
        return {
            key: {"wr": self._wr(values), "n": len(values)}
            for key, values in groups.items()
            if len(values) >= MIN_SAMPLE
        }

    def _by_factor(self, closed: list) -> dict:
        all_factors = set()
        for trade in closed:
            all_factors.update(trade.get("factors", []))
        result = {}
        for factor in sorted(all_factors):
            subset = [trade for trade in closed if factor in trade.get("factors", [])]
            if len(subset) >= MIN_SAMPLE:
                result[factor] = {"wr": self._wr(subset), "n": len(subset)}
        return result

    def _by_tag(self, closed: list) -> dict:
        all_tags = set()
        for trade in closed:
            all_tags.update(trade.get("tags", []))
        result = {}
        for tag in sorted(all_tags):
            subset = [trade for trade in closed if tag in trade.get("tags", [])]
            if len(subset) >= MIN_SAMPLE:
                result[tag] = {"wr": self._wr(subset), "n": len(subset)}
        return result

    def _by_symbol(self, closed: list) -> dict:
        return self._bucket(closed, lambda trade: trade.get("symbol", "UNKNOWN"))

    def _by_regime(self, closed: list) -> dict:
        return self._bucket(closed, lambda trade: trade.get("regime", "UNKNOWN"))

    def _by_grade(self, closed: list) -> dict:
        # Version is part of the bucket key: unlike grade labels across
        # different semantics, these values must never be combined.
        return self._bucket(
            closed,
            lambda trade: str(trade.get("grade_schema_version")) + ":" + str(trade.get("grade", "UNKNOWN")),
        )

    def _by_score_bucket(self, closed: list) -> dict:
        def bucket_label(trade):
            score = trade.get("obi_score") or 0
            if score < 40:
                return "0-39"
            if score < 55:
                return "40-54"
            if score < 70:
                return "55-69"
            return "70+"
        return self._bucket(closed, bucket_label)

    def _top_factors(self, closed: list) -> list:
        all_factors = set()
        for trade in closed:
            all_factors.update(trade.get("factors", []))
        ranked = []
        for factor in all_factors:
            subset = [trade for trade in closed if factor in trade.get("factors", [])]
            if len(subset) >= MIN_SAMPLE:
                ranked.append((factor, self._wr(subset), len(subset)))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return [{"factor": factor, "wr": win_rate, "n": count} for factor, win_rate, count in ranked[:3]]

    def _print_report(self, report: dict) -> None:
        print("[FACTOR] ── Factor Analytics Report ──────────────────")
        print("[FACTOR] Sample: " + str(report["sample_size"]) + " terminal events | WR: " + str(report["overall_wr"]) + "%")
        print("[FACTOR] By Factor:")
        for factor, data in sorted(report["by_factor"].items(), key=lambda item: item[1]["wr"], reverse=True):
            print("[FACTOR]   " + factor + ": " + str(data["wr"]) + "% (" + str(data["n"]) + " trades)")
        print("[FACTOR] By Tag:")
        for tag, data in sorted(report["by_tag"].items(), key=lambda item: item[1]["wr"], reverse=True):
            print("[FACTOR]   " + tag + ": " + str(data["wr"]) + "% (" + str(data["n"]) + " trades)")
        print("[FACTOR] By Symbol:")
        for symbol, data in sorted(report["by_symbol"].items(), key=lambda item: item[1]["wr"], reverse=True):
            print("[FACTOR]   " + symbol + ": " + str(data["wr"]) + "% (" + str(data["n"]) + " trades)")
        print("[FACTOR] By Regime:")
        for regime, data in report["by_regime"].items():
            print("[FACTOR]   " + regime + ": " + str(data["wr"]) + "% (" + str(data["n"]) + " trades)")
        print("[FACTOR] By Grade:")
        for grade, data in sorted(report["by_grade"].items(), key=lambda item: item[1]["wr"], reverse=True):
            print("[FACTOR]   " + grade + ": " + str(data["wr"]) + "% (" + str(data["n"]) + " trades)")
        print("[FACTOR] By Score Bucket:")
        for bucket, data in sorted(report["by_score"].items()):
            print("[FACTOR]   Score " + bucket + ": " + str(data["wr"]) + "% (" + str(data["n"]) + " trades)")
        print("[FACTOR] Top 3 Factors:")
        for item in report["top_factors"]:
            print("[FACTOR]   " + item["factor"] + ": " + str(item["wr"]) + "% (" + str(item["n"]) + " trades)")
        print("[FACTOR] ─────────────────────────────────────────────")
