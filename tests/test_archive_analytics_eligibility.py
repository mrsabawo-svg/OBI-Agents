import unittest
from unittest.mock import patch

from core.archive_analytics_eligibility import (
    GRADE_SCHEMA_VERSION,
    LIFECYCLE_SCHEMA_VERSION,
    eligibility_reasons,
    select_eligible_events,
)
from core.decision_lineage import _sha256


def eligible_event(event_id="EVENT-001", outcome="TP3", grade_version=GRADE_SCHEMA_VERSION):
    setup_id = "c" * 64
    record = {
        "id": event_id,
        "symbol": "EURUSD",
        "opened": "2026-11-01 10:00 SAST",
        "timestamp": "2026-11-01 10:00 SAST",
        "direction": "BUY",
        "entry": 1.1000,
        "sl": 1.0990,
        "tp1": 1.1010,
        "tp2": 1.1020,
        "tp3": 1.1030,
        "status": "CLOSED",
        "outcome": outcome,
        "terminal_outcome": outcome,
        "lifecycle_schema_version": LIFECYCLE_SCHEMA_VERSION,
        "grade_schema_version": grade_version,
        "grade": "A",
        "obi_score": 88,
        "tags": ["OB"],
        "factors": ["HTF", "MTF"],
        "regime": "TRENDING",
        "setup_identity_status": "complete",
        "setup_identity_version": "setup-v1",
        "setup_id": setup_id,
        "data_lineage_status": "complete",
        "data_lineage": {
            "schema_version": "obi-data-lineage-v5",
            "instrument_id": "EURUSD",
            "status": "complete",
            "timeframes": {
                "1h": {
                    "status": "canonical",
                    "instrument_verification_status": "verified",
                    "instrument_verification_id": "verification-001",
                    "instrument_metadata_sha256": "a" * 64,
                    "broker_symbol": "EURUSD",
                    "broker": "ExampleBroker",
                    "account_fingerprint": "b" * 64,
                    "canonical_candle_ids_tail": ["candle-1"],
                    "raw_observation_ids_tail": ["raw-1"],
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
            "instrument_id": "EURUSD",
            "signal_identity": {
                "signal_id": event_id,
                "setup_id": setup_id,
                "setup_identity_version": "setup-v1",
            },
        },
    }
    record["decision_lineage"]["data_lineage_sha256"] = _sha256(record["data_lineage"])
    record["decision_lineage"]["manifest_sha256"] = "e" * 64
    record["decision_lineage_sha256"] = "e" * 64
    return record


class ArchiveAnalyticsEligibilityTest(unittest.TestCase):
    def setUp(self):
        self.verify_patch = patch(
            "core.archive_analytics_eligibility.verify_decision_lineage",
            return_value=True,
        )
        self.verify_patch.start()
        self.addCleanup(self.verify_patch.stop)

    def test_tp3_and_sl_are_the_only_eligible_terminal_outcomes(self):
        tp3 = eligible_event("TP3-EVENT", "TP3")
        sl = eligible_event("SL-EVENT", "SL")
        selection = select_eligible_events([tp3, sl])
        self.assertEqual(
            {event["terminal_outcome"] for event in selection["events"]},
            {"TP3", "SL"},
        )
        self.assertEqual(len(selection["events"]), 2)

    def test_legacy_tp1_closed_record_without_terminal_outcome_is_excluded(self):
        record = eligible_event(outcome="TP1")
        record["terminal_outcome"] = None
        reasons = eligibility_reasons(record)
        self.assertIn("non_terminal_or_unsupported_outcome", reasons)
        self.assertEqual(select_eligible_events([record])["events"], [])

    def test_tp1_and_tp2_milestones_are_not_terminal_wins(self):
        for milestone in ("TP1", "TP2"):
            record = eligible_event(outcome=milestone)
            record["terminal_outcome"] = None
            self.assertEqual(select_eligible_events([record])["events"], [])

    def test_malformed_terminal_outcome_is_rejected_without_error(self):
        record = eligible_event()
        record["terminal_outcome"] = {"value": "TP3"}
        self.assertIn("non_terminal_or_unsupported_outcome", eligibility_reasons(record))
        self.assertEqual(select_eligible_events([record])["events"], [])

    def test_expired_event_is_excluded(self):
        record = eligible_event(outcome="EXPIRED")
        self.assertEqual(select_eligible_events([record])["events"], [])

    def test_ambiguous_event_is_excluded(self):
        record = eligible_event()
        record["lifecycle_event"] = "AMBIGUOUS"
        record["resolution_note"] = "AMBIGUOUS_SAME_CANDLE_TARGET_AND_STOP"
        self.assertIn("ambiguous_lifecycle", eligibility_reasons(record))
        self.assertEqual(select_eligible_events([record])["events"], [])

    def test_prior_ambiguous_lifecycle_event_remains_quarantined(self):
        record = eligible_event()
        record["resolution_note"] = None
        record["lifecycle_events"] = [
            {"event": "AMBIGUOUS", "note": "AMBIGUOUS_SAME_CANDLE_TARGET_AND_STOP"}
        ]
        selection = select_eligible_events([record])
        self.assertEqual(selection["events"], [])
        self.assertEqual(len(selection["quarantined"]), 1)

    def test_partial_lineage_is_excluded(self):
        record = eligible_event()
        record["data_lineage_status"] = "partial"
        record["data_lineage"]["status"] = "partial"
        self.assertIn("incomplete_or_unverified_data_lineage", eligibility_reasons(record))
        self.assertEqual(select_eligible_events([record])["events"], [])

    def test_unverified_instrument_is_excluded(self):
        record = eligible_event()
        record["data_lineage"]["timeframes"]["1h"]["instrument_verification_status"] = "unverified"
        self.assertEqual(select_eligible_events([record])["events"], [])

    def test_missing_broker_symbol_is_excluded(self):
        record = eligible_event()
        record["data_lineage"]["timeframes"]["1h"]["broker_symbol"] = None
        self.assertEqual(select_eligible_events([record])["events"], [])

    def test_incomplete_decision_provenance_is_excluded(self):
        record = eligible_event()
        record["decision_lineage_provenance_status"] = "partial"
        self.assertIn("incomplete_or_unverified_decision_provenance", eligibility_reasons(record))
        self.assertEqual(select_eligible_events([record])["events"], [])

    def test_tampered_archived_data_lineage_is_excluded(self):
        record = eligible_event()
        record["data_lineage"]["timeframes"]["1h"]["broker_symbol"] = "OTHER"
        self.assertEqual(select_eligible_events([record])["events"], [])

    def test_decision_manifest_digest_mismatch_is_excluded(self):
        record = eligible_event()
        record["decision_lineage_sha256"] = "f" * 64
        self.assertEqual(select_eligible_events([record])["events"], [])

    def test_decision_identity_mismatch_is_excluded(self):
        record = eligible_event()
        record["decision_lineage"]["signal_identity"]["signal_id"] = "OTHER-EVENT"
        self.assertEqual(select_eligible_events([record])["events"], [])

    def test_missing_lifecycle_version_is_excluded(self):
        record = eligible_event()
        record.pop("lifecycle_schema_version")
        self.assertIn("unversioned_lifecycle_semantics", eligibility_reasons(record))
        self.assertEqual(select_eligible_events([record])["events"], [])

    def test_unversioned_grade_is_excluded(self):
        record = eligible_event()
        record.pop("grade_schema_version")
        self.assertIn("unversioned_grade_semantics", eligibility_reasons(record))
        self.assertEqual(select_eligible_events([record])["events"], [])

    def test_duplicate_representations_count_once(self):
        first = eligible_event()
        second = dict(first)
        selection = select_eligible_events([first, second])
        self.assertEqual(len(selection["events"]), 1)
        self.assertEqual(selection["duplicate_count"], 1)
        self.assertEqual(selection["quarantined"], [])

    def test_conflicting_duplicate_structure_is_quarantined(self):
        first = eligible_event()
        second = eligible_event()
        second["entry"] = 1.1005
        selection = select_eligible_events([first, second])
        self.assertEqual(selection["events"], [])
        self.assertEqual(len(selection["quarantined"]), 1)

    def test_conflicting_terminal_outcomes_are_quarantined(self):
        first = eligible_event(outcome="TP3")
        second = eligible_event(outcome="SL")
        selection = select_eligible_events([first, second])
        self.assertEqual(selection["events"], [])
        self.assertEqual(len(selection["quarantined"]), 1)

    def test_legacy_identity_fallback_is_deterministic_but_not_eligible(self):
        first = eligible_event()
        second = dict(first)
        first.pop("id")
        second.pop("id")
        first["decision_lineage"]["signal_identity"]["signal_id"] = None
        second["decision_lineage"]["signal_identity"]["signal_id"] = None
        first["setup_identity_status"] = "unavailable"
        second["setup_identity_status"] = "unavailable"
        first["setup_id"] = None
        second["setup_id"] = None
        first["setup_identity_version"] = None
        second["setup_identity_version"] = None
        selection = select_eligible_events([first, second])
        self.assertEqual(selection["events"], [])
        self.assertEqual(selection["duplicate_count"], 1)

    def test_snapshot_hash_is_independent_of_archive_order(self):
        first = eligible_event("EVENT-A")
        second = eligible_event("EVENT-B", "SL")
        forward = select_eligible_events([first, second])
        reverse = select_eligible_events([second, first])
        self.assertEqual(forward["snapshot_sha256"], reverse["snapshot_sha256"])

    def test_grade_versions_remain_separate(self):
        first = eligible_event("GRADE-V1", "TP3", "grade-v1")
        second = eligible_event("GRADE-V2", "SL", "grade-v2")
        selection = select_eligible_events([first, second])
        self.assertEqual(len(selection["events"]), 2)
        self.assertEqual(
            {event["grade_schema_version"] for event in selection["events"]},
            {"grade-v1", "grade-v2"},
        )


if __name__ == "__main__":
    unittest.main()
