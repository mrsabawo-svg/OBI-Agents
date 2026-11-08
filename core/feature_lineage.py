"""Versioned definitions for DataAgent-derived indicator columns."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
import math
from typing import Any


FEATURE_LINEAGE_SCHEMA_VERSION = "obi-feature-lineage-v1"
INDICATOR_FEATURE_SET_VERSION = "data-agent-indicators-v1"
CALCULATION_LIBRARY = "ta"

INDICATOR_DEFINITIONS = (
    {
        "name": "ADX_14",
        "column": "ADX_14",
        "formula": "ta.trend.adx",
        "inputs": ["High", "Low", "Close"],
        "parameters": {"window": 14, "fillna": False},
    },
    {
        "name": "EMA_20",
        "column": "EMA_20",
        "formula": "ta.trend.ema_indicator",
        "inputs": ["Close"],
        "parameters": {"window": 20, "fillna": False},
    },
    {
        "name": "EMA_50",
        "column": "EMA_50",
        "formula": "ta.trend.ema_indicator",
        "inputs": ["Close"],
        "parameters": {"window": 50, "fillna": False},
    },
    {
        "name": "ATRr_14",
        "column": "ATRr_14",
        "formula": "ta.volatility.average_true_range",
        "inputs": ["High", "Low", "Close"],
        "parameters": {"window": 14, "fillna": False},
    },
)


def installed_calculation_library_version() -> str:
    """Return the installed ta package version without guessing."""
    try:
        return version(CALCULATION_LIBRARY)
    except PackageNotFoundError:
        return "unknown"


def build_indicator_lineage(
    frame: Any,
    library_version: str | None = None,
) -> dict:
    """Describe the indicator formulas and whether their output columns exist."""
    columns = set(getattr(frame, "columns", []))
    resolved_version = library_version or installed_calculation_library_version()
    features = []
    for definition in INDICATOR_DEFINITIONS:
        column_present = definition["column"] in columns
        latest_value_valid = False
        if column_present and len(frame):
            try:
                latest_value = float(frame[definition["column"]].iloc[-1])
                latest_value_valid = math.isfinite(latest_value)
            except (TypeError, ValueError, OverflowError, KeyError, IndexError):
                latest_value_valid = False
        features.append({
            **definition,
            "column_present": column_present,
            "latest_value_valid": latest_value_valid,
            "available": bool(column_present and latest_value_valid),
        })

    available_count = sum(1 for feature in features if feature["available"])
    if available_count == len(features) and resolved_version != "unknown":
        status = "complete"
    elif available_count:
        status = "partial"
    else:
        status = "unavailable"

    return {
        "schema_version": FEATURE_LINEAGE_SCHEMA_VERSION,
        "feature_set_version": INDICATOR_FEATURE_SET_VERSION,
        "calculation_library": CALCULATION_LIBRARY,
        "calculation_library_version": resolved_version,
        "status": status,
        "feature_count": len(features),
        "available_count": available_count,
        "features": features,
    }
