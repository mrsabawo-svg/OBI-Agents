import unittest

import numpy as np
import pandas as pd

from agents.data_agent import DataAgent
from core.feature_lineage import (
    INDICATOR_FEATURE_SET_VERSION,
    build_indicator_lineage,
)


class FeatureLineageTest(unittest.TestCase):
    def test_feature_manifest_records_formula_parameters_and_library_version(self):
        frame = pd.DataFrame(columns=["ADX_14", "EMA_20", "EMA_50", "ATRr_14"])

        manifest = build_indicator_lineage(frame, library_version="0.11.0")

        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["feature_set_version"], INDICATOR_FEATURE_SET_VERSION)
        self.assertEqual(manifest["calculation_library_version"], "0.11.0")
        definitions = {feature["name"]: feature for feature in manifest["features"]}
        self.assertEqual(definitions["EMA_50"]["formula"], "ta.trend.ema_indicator")
        self.assertEqual(definitions["EMA_50"]["parameters"]["window"], 50)
        self.assertTrue(all(feature["available"] for feature in manifest["features"]))

    def test_missing_indicator_columns_are_explicitly_partial(self):
        frame = pd.DataFrame(columns=["EMA_20", "EMA_50"])

        manifest = build_indicator_lineage(frame, library_version="0.11.0")

        self.assertEqual(manifest["status"], "partial")
        self.assertEqual(manifest["available_count"], 2)
        missing = {
            feature["name"] for feature in manifest["features"]
            if not feature["available"]
        }
        self.assertEqual(missing, {"ADX_14", "ATRr_14"})

    def test_unknown_calculation_library_version_is_not_complete(self):
        frame = pd.DataFrame(columns=["ADX_14", "EMA_20", "EMA_50", "ATRr_14"])

        manifest = build_indicator_lineage(frame, library_version="unknown")

        self.assertEqual(manifest["status"], "partial")

    def test_data_agent_attaches_indicator_lineage_to_computed_frame(self):
        index = pd.date_range("2026-11-01T00:00:00Z", periods=80, freq="5min")
        close = np.linspace(100.0, 110.0, len(index))
        frame = pd.DataFrame({
            "Open": close - 0.1,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": np.full(len(index), 100.0),
        }, index=index)

        result = DataAgent("SFX_VOL_75")._add_indicators(frame)

        lineage = result.attrs["feature_lineage"]
        self.assertEqual(lineage["status"], "complete")
        self.assertEqual(lineage["available_count"], 4)
        self.assertEqual(
            {feature["name"] for feature in lineage["features"]},
            {"ADX_14", "EMA_20", "EMA_50", "ATRr_14"},
        )


if __name__ == "__main__":
    unittest.main()
