import unittest
from unittest.mock import patch

from agents.edge_agent import EdgeAgent
from core.archive_analytics_eligibility import GRADE_SCHEMA_VERSION, LIFECYCLE_SCHEMA_VERSION
from core.models import TriggerResult


def eligible_edge_record(index, outcome):
    event_id = "EDGE-" + str(index)
    setup_id = "c" * 64
    opened = "2026-11-01 10:" + str(index).zfill(2) + " SAST"
    record = {
        "id": event_id,
        "symbol": "XAUUSD",
        "opened": opened,
        "timestamp": opened,
        "direction": "BUY",
        "entry": 2000.0 + index,
        "sl": 1990.0 + index,
        "tp1": 2010.0 + index,
        "tp2": 2020.0 + index,
        "tp3": 2030.0 + index,
        "status": "CLOSED",
        "outcome": outcome,
        "terminal_outcome": outcome,
        "lifecycle_schema_version": LIFECYCLE_SCHEMA_VERSION,
        "grade_schema_version": GRADE_SCHEMA_VERSION,
        "grade": "A" if outcome == "TP3" else "C",
        "regime": "TRENDING",
        "tags": ["OB"],
        "factors": ["HTF"],
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
                    "instrument_verification_id": "verification-001",
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
    from core.archive_analytics_eligibility import _canonical_hash
    record["decision_lineage"]["data_lineage_sha256"] = _canonical_hash(record["data_lineage"])
    record["decision_lineage"]["manifest_sha256"] = "e" * 64
    record["decision_lineage_sha256"] = "e" * 64
    return record


class TestEdgeDecoupling(unittest.TestCase):
    def test_trigger_grade_does_not_affect_edge_win_rate(self):
        archive = [
            eligible_edge_record(index, "TP3" if index < 15 else "SL")
            for index in range(25)
        ]
        mock_memory = {"_archive": archive}

        trigger_c = TriggerResult(
            fire=True, direction="BUY", grade="C", entry=2000.0, sl=1990.0,
            tp1=2010.0, tp2=2020.0, tp3=2030.0, rr=2.0, confluence=3,
            tags=["OB"], reason="Test signal"
        )
        trigger_a = TriggerResult(
            fire=True, direction="BUY", grade="A", entry=2000.0, sl=1990.0,
            tp1=2010.0, tp2=2020.0, tp3=2030.0, rr=2.0, confluence=3,
            tags=["OB"], reason="Test signal"
        )

        with patch("agents.edge_agent.load_memory", return_value=mock_memory), patch(
            "core.archive_analytics_eligibility.verify_decision_lineage",
            return_value=True,
        ):
            edge_agent = EdgeAgent("XAUUSD")
            result_c = edge_agent.analyse(trigger_c, None, {"label": "TRENDING"})
            result_a = edge_agent.analyse(trigger_a, None, {"label": "TRENDING"})

        # Current trigger grade must not affect the historical snapshot or edge.
        self.assertEqual(result_c.grade_wr, result_a.grade_wr)
        self.assertEqual(result_c.overall_wr, 60.0)
        self.assertEqual(result_c.grade_wr, 60.0)
        self.assertEqual(result_a.grade_wr, 60.0)
        self.assertEqual(result_c.sample_size, 25)
        self.assertEqual(edge_agent.archive_eligibility_summary["eligible_count"], 25)
        self.assertEqual(edge_agent.archive_eligibility_summary["excluded_count"], 0)


if __name__ == "__main__":
    unittest.main()
