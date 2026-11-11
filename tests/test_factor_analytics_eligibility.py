import unittest
from unittest.mock import patch

from agents.factor_agent import FactorAgent
from core.archive_analytics_eligibility import GRADE_SCHEMA_VERSION, LIFECYCLE_SCHEMA_VERSION


def eligible_factor_event(index, outcome):
    event_id = "FACTOR-" + str(index)
    setup_id = "d" * 64
    opened = "2026-11-02 11:" + str(index).zfill(2) + " SAST"
    record = {
        "id": event_id,
        "symbol": "XAUUSD",
        "opened": opened,
        "timestamp": opened,
        "direction": "BUY",
        "entry": 4000.0 + index,
        "sl": 3990.0 + index,
        "tp1": 4010.0 + index,
        "tp2": 4020.0 + index,
        "tp3": 4030.0 + index,
        "status": "CLOSED",
        "outcome": outcome,
        "terminal_outcome": outcome,
        "lifecycle_schema_version": LIFECYCLE_SCHEMA_VERSION,
        "grade_schema_version": GRADE_SCHEMA_VERSION,
        "grade": "A",
        "obi_score": 88,
        "factors": ["HTF", "MTF"],
        "tags": ["OB"],
        "regime": "TRENDING",
        "setup_identity_status": "complete",
        "setup_identity_version": "setup-v1",
        "setup_id": setup_id,
        "data_lineage_status": "complete",
        "data_lineage": {
            "instrument_id": "XAUUSD",
            "status": "complete",
            "timeframes": {
                "1h": {
                    "status": "canonical",
                    "instrument_verification_status": "verified",
                    "instrument_verification_id": "verification-002",
                    "instrument_metadata_sha256": "a" * 64,
                    "broker_symbol": "GC=F",
                    "broker": "ExampleBroker",
                    "account_fingerprint": "b" * 64,
                    "canonical_candle_ids_tail": ["candle-" + str(index)],
                    "raw_observation_ids_tail": ["raw-" + str(index)],
                }
            },
        },
        "decision_lineage_status": "complete",
        "decision_lineage_provenance_status": "complete",
        "decision_lineage_identity_status": "bound",
        "decision_lineage": {
            "status": "complete",
            "provenance_status": "complete",
            "data_lineage_status": "complete",
            "instrument_id": "XAUUSD",
            "signal_identity": {
                "signal_id": event_id,
                "setup_id": setup_id,
                "setup_identity_version": "setup-v1",
            },
        },
    }
    from core.decision_lineage import _sha256
    record["decision_lineage"]["data_lineage_sha256"] = _sha256(record["data_lineage"])
    record["decision_lineage"]["manifest_sha256"] = "e" * 64
    record["decision_lineage_sha256"] = "e" * 64
    return record


class FactorAnalyticsEligibilityTest(unittest.TestCase):
    def test_factor_agent_uses_only_shared_eligible_terminal_snapshot(self):
        archive = [
            eligible_factor_event(index, "TP3" if index < 4 else "SL")
            for index in range(5)
        ]
        # A legacy TP1 closure must not inflate the factor win rate.
        legacy = {
            "id": "LEGACY-TP1",
            "symbol": "XAUUSD",
            "opened": "2026-10-01 10:00 SAST",
            "timestamp": "2026-10-01 10:00 SAST",
            "direction": "BUY",
            "entry": 4000.0,
            "sl": 3990.0,
            "tp1": 4010.0,
            "tp2": 4020.0,
            "tp3": 4030.0,
            "status": "CLOSED",
            "outcome": "TP1",
            "factors": ["HTF", "MTF"],
            "obi_score": 88,
        }
        archive.append(legacy)
        memory = {"_archive": archive}

        with patch("agents.factor_agent.load_memory", return_value=memory), patch(
            "agents.factor_agent.save_memory", return_value=True
        ), patch(
            "core.archive_analytics_eligibility.verify_decision_lineage",
            return_value=True,
        ):
            report = FactorAgent().analyse()

        self.assertEqual(report["sample_size"], 5)
        self.assertEqual(report["overall_wr"], 80.0)
        self.assertEqual(report["by_factor"]["HTF"], {"wr": 80.0, "n": 5})
        self.assertEqual(report["by_factor"]["MTF"], {"wr": 80.0, "n": 5})
        self.assertEqual(memory["_factor_analytics_eligibility"]["eligible_count"], 5)
        self.assertEqual(memory["_factor_analytics_eligibility"]["excluded_count"], 1)

    def test_factor_agent_clears_stale_report_when_eligible_sample_is_too_small(self):
        memory = {
            "_archive": [{
                "id": "LEGACY-ROW",
                "status": "CLOSED",
                "outcome": "TP1",
                "symbol": "XAUUSD",
            }],
            "_factor_analytics": {"sample_size": 100, "overall_wr": 99.0},
        }
        with patch("agents.factor_agent.load_memory", return_value=memory), patch(
            "agents.factor_agent.save_memory", return_value=True
        ):
            report = FactorAgent().analyse()
        self.assertEqual(report, {})
        self.assertEqual(memory["_factor_analytics"], {})
        self.assertEqual(memory["_factor_analytics_eligibility"]["eligible_count"], 0)

    def test_factor_grade_buckets_keep_schema_versions_separate(self):
        agent = FactorAgent()
        records = []
        for index in range(5):
            record = eligible_factor_event(index, "TP3")
            record["grade_schema_version"] = "grade-v1"
            records.append(record)
        for index in range(5, 10):
            record = eligible_factor_event(index, "SL")
            record["grade_schema_version"] = "grade-v2"
            records.append(record)

        result = agent._by_grade(records)
        self.assertEqual(result["grade-v1:A"], {"wr": 100.0, "n": 5})
        self.assertEqual(result["grade-v2:A"], {"wr": 0.0, "n": 5})


if __name__ == "__main__":
    unittest.main()
