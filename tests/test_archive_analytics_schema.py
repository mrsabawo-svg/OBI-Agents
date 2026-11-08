import unittest

import pandas as pd

from agents.archive_agent import ArchiveAgent
from agents.factor_agent import FactorAgent
from core.data_lineage import build_data_lineage


class FakeScore:
    confidence = 88
    grade = "A+"


class ArchiveAnalyticsSchemaTest(unittest.TestCase):
    def _signal(self, score):
        return {
            "id": "M1_SCHEMA_TEST",
            "symbol": "XAUUSD",
            "score": score,
            "regime": {"label": "TRENDING", "confidence": 90},
            "trigger": {
                "direction": "BUY",
                "grade": "C",
                "entry": 4000.0,
                "sl": 3990.0,
                "tp1": 4010.0,
                "tp2": 4020.0,
                "tp3": 4030.0,
                "rr": 3.0,
                "tags": ["OB"],
                "confluence": 4,
            },
            "bias": {"score": 80, "factors": ["HTF", "MTF"]},
            "htf": {},
            "mtf": {},
        }

    def test_archive_preserves_factor_agent_schema_for_score_object(self):
        entry = ArchiveAgent()._build_entry(self._signal(FakeScore()))
        self.assertEqual(entry["factors"], ["HTF", "MTF"])
        self.assertEqual(entry["bias_factors"], ["HTF", "MTF"])
        self.assertEqual(entry["obi_score"], 88)
        self.assertEqual(entry["grade"], "A+")
        self.assertEqual(entry["trigger_grade"], "C")

    def test_archive_preserves_factor_agent_schema_for_score_dict(self):
        entry = ArchiveAgent()._build_entry(
            self._signal({"confidence": 73, "grade": "A"})
        )
        self.assertEqual(entry["factors"], ["HTF", "MTF"])
        self.assertEqual(entry["obi_score"], 73)
        self.assertEqual(entry["grade"], "A")

    def test_archive_preserves_data_lineage_manifest(self):
        signal = self._signal(FakeScore())
        lineage = {
            "schema_version": "obi-data-lineage-v1",
            "instrument_id": "XAUUSD",
            "status": "complete",
            "timeframes": {
                "5m": {
                    "status": "canonical",
                    "raw_batch_id": "batch-001",
                    "validation_report_id": "report-001",
                    "canonical_candle_ids_tail": ["candle-1", "candle-2"],
                }
            },
        }
        signal["data_lineage"] = lineage

        entry = ArchiveAgent()._build_entry(signal)

        self.assertEqual(entry["data_lineage_status"], "complete")
        self.assertEqual(entry["data_lineage"]["timeframes"]["5m"]["raw_batch_id"], "batch-001")
        lineage["timeframes"]["5m"]["canonical_candle_ids_tail"].append("later-mutation")
        self.assertEqual(
            entry["data_lineage"]["timeframes"]["5m"]["canonical_candle_ids_tail"],
            ["candle-1", "candle-2"],
        )

    def test_factor_agent_consumes_restored_fields(self):
        agent = FactorAgent()
        closed = [
            {
                "status": "CLOSED",
                "outcome": "TP1" if index < 4 else "SL",
                "factors": ["HTF", "MTF"],
                "obi_score": 88,
            }
            for index in range(5)
        ]
        by_factor = agent._by_factor(closed)
        by_score = agent._by_score_bucket(closed)
        self.assertEqual(by_factor["HTF"], {"wr": 80.0, "n": 5})
        self.assertEqual(by_factor["MTF"], {"wr": 80.0, "n": 5})
        self.assertEqual(by_score["70+"], {"wr": 80.0, "n": 5})


if __name__ == "__main__":
    unittest.main()



class DataLineageManifestTest(unittest.TestCase):
    def test_builds_bounded_canonical_lineage_and_marks_missing_timeframe(self):
        index = pd.date_range("2026-11-08T00:00:00Z", periods=4, freq="5min")
        frame = pd.DataFrame({
            "CanonicalCandleID": ["c-1", "c-2", "c-3", "c-4"],
            "RawObservationID": ["r-1", "r-2", "r-3", "r-4"],
        }, index=index)
        frame.attrs["source_metadata"] = {
            "raw_batch_id": "batch-001",
            "validation_report_id": "report-001",
            "canonical_validation_report_sha256": "a" * 64,
            "broker": "Weltrade",
            "server_name": "Weltrade-Demo",
            "account_environment": "demo",
            "broker_symbol": "SFX Vol 75",
            "source_type": "broker_bars",
        }

        manifest = build_data_lineage(
            {"5m": frame},
            instrument_id="SFX_VOL_75",
            expected_timeframes=["5m", "1h"],
            max_id_window=2,
        )

        self.assertEqual(manifest["status"], "partial")
        self.assertEqual(manifest["timeframes"]["5m"]["status"], "canonical")
        self.assertEqual(
            manifest["timeframes"]["5m"]["canonical_candle_ids_tail"],
            ["c-3", "c-4"],
        )
        self.assertEqual(
            manifest["timeframes"]["5m"]["raw_observation_ids_tail"],
            ["r-3", "r-4"],
        )
        self.assertTrue(manifest["timeframes"]["5m"]["ids_truncated"])
        self.assertEqual(manifest["timeframes"]["1h"]["status"], "missing")

    def test_untracked_market_data_is_not_mislabelled_canonical(self):
        frame = pd.DataFrame({"Close": [100, 101]})
        manifest = build_data_lineage(
            {"1h": frame},
            instrument_id="EURUSD",
            expected_timeframes=["1h"],
        )

        self.assertEqual(manifest["status"], "untracked")
        self.assertEqual(manifest["timeframes"]["1h"]["status"], "untracked")
        self.assertEqual(manifest["timeframes"]["1h"]["canonical_candle_ids_tail"], [])

    def test_null_canonical_ids_make_lineage_partial(self):
        frame = pd.DataFrame({
            "CanonicalCandleID": ["c-1", None],
            "RawObservationID": ["r-1", "r-2"],
        })
        frame.attrs["source_metadata"] = {
            "raw_batch_id": "batch-001",
            "validation_report_id": "report-001",
            "canonical_validation_report_sha256": "a" * 64,
        }

        manifest = build_data_lineage({"5m": frame}, "SFX_VOL_75", ["5m"])

        self.assertEqual(manifest["status"], "partial")
        self.assertEqual(manifest["timeframes"]["5m"]["status"], "partial")
        self.assertEqual(manifest["timeframes"]["5m"]["canonical_candle_ids_tail"], [])
