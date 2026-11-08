"""Build compact, traceable source manifests for archived OBI signals.

The manifest points to immutable raw batches and validation reports. It keeps a
bounded tail of candle IDs for quick inspection instead of duplicating market
history inside the signal archive.
"""
from __future__ import annotations

from typing import Any


LINEAGE_SCHEMA_VERSION = "obi-data-lineage-v1"
DEFAULT_ID_WINDOW = 50
QUALITY_SUMMARY_FIELDS = (
    "received_count",
    "durable_quarantined_count",
    "durable_quarantine_record_count",
    "invalid_timestamp_count",
    "invalid_numeric_count",
    "invalid_ohlc_count",
    "duplicate_timestamp_count",
    "invalid_bar_status_count",
    "invalid_volume_type_count",
    "forming_excluded_count",
)
REQUIRED_CANONICAL_METADATA = (
    "raw_batch_id",
    "validation_report_id",
    "canonical_validation_report_sha256",
    "broker",
    "server_name",
    "account_environment",
    "broker_symbol",
    "source_type",
)


def _time_label(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return str(value)


def _column_values(frame: Any, column: str) -> list[str]:
    try:
        values = frame[column].tolist()
    except Exception:
        return []
    normalized = []
    for value in values:
        if value is None:
            normalized.append("")
            continue
        try:
            if value != value:
                normalized.append("")
                continue
        except Exception:
            pass
        label = str(value).strip()
        normalized.append("" if label.lower() in {"", "nan", "none", "<na>"} else label)
    return normalized


def _timeframe_manifest(frame: Any, timeframe: str, max_id_window: int) -> dict:
    row_count = len(frame)
    if row_count == 0:
        return {
            "status": "missing",
            "timeframe": timeframe,
            "row_count": 0,
            "canonical_candle_ids_tail": [],
            "raw_observation_ids_tail": [],
        }

    attrs = getattr(frame, "attrs", {}) or {}
    metadata = attrs.get("source_metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    quality_report = attrs.get("quality_report", {})
    if not isinstance(quality_report, dict):
        quality_report = {}
    quality_summary = {
        key: value for key, value in quality_report.items()
        if key in QUALITY_SUMMARY_FIELDS
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    }

    columns = set(getattr(frame, "columns", []))
    has_lineage_columns = {
        "CanonicalCandleID", "RawObservationID"
    }.issubset(columns)

    canonical_ids = _column_values(frame, "CanonicalCandleID") if has_lineage_columns else []
    raw_ids = _column_values(frame, "RawObservationID") if has_lineage_columns else []
    ids_complete = (
        has_lineage_columns
        and len(canonical_ids) == row_count
        and len(raw_ids) == row_count
        and all(canonical_ids)
        and all(raw_ids)
    )
    metadata_complete = all(
        isinstance(metadata.get(key), str) and metadata.get(key).strip()
        for key in REQUIRED_CANONICAL_METADATA
    )
    report_hash = metadata.get("canonical_validation_report_sha256", "")
    metadata_complete = metadata_complete and (
        len(report_hash) == 64
        and all(char in "0123456789abcdef" for char in report_hash.lower())
    )

    if ids_complete and metadata_complete:
        status = "canonical"
    elif ids_complete or any(metadata.get(key) for key in REQUIRED_CANONICAL_METADATA):
        status = "partial"
    else:
        status = "untracked"

    index_values = list(getattr(frame, "index", []))
    first_time = _time_label(index_values[0]) if index_values else None
    last_time = _time_label(index_values[-1]) if index_values else None
    window = max(1, int(max_id_window))
    return {
        "status": status,
        "timeframe": timeframe,
        "row_count": row_count,
        "first_bar_open_time": first_time,
        "last_bar_open_time": last_time,
        "raw_batch_id": metadata.get("raw_batch_id"),
        "validation_report_id": metadata.get("validation_report_id"),
        "validation_report_sha256": metadata.get("canonical_validation_report_sha256"),
        "broker": metadata.get("broker"),
        "server_name": metadata.get("server_name"),
        "account_environment": metadata.get("account_environment"),
        "broker_symbol": metadata.get("broker_symbol"),
        "source_type": metadata.get("source_type"),
        "quality_summary": quality_summary,
        "canonical_candle_ids_tail": canonical_ids[-window:] if ids_complete else [],
        "raw_observation_ids_tail": raw_ids[-window:] if ids_complete else [],
        "id_window_count": min(row_count, window) if ids_complete else 0,
        "ids_truncated": bool(ids_complete and row_count > window),
    }


def build_data_lineage(
    market_data: dict,
    instrument_id: str,
    expected_timeframes: list[str] | None = None,
    max_id_window: int = DEFAULT_ID_WINDOW,
) -> dict:
    """Return a JSON-safe, bounded manifest for the frames used by one scan."""
    if not isinstance(market_data, dict):
        market_data = {}
    if not isinstance(instrument_id, str) or not instrument_id.strip():
        raise ValueError("instrument_id must be a non-empty string")
    if isinstance(max_id_window, bool) or not isinstance(max_id_window, int) or max_id_window < 1:
        raise ValueError("max_id_window must be a positive integer")

    timeframes = list(expected_timeframes or market_data.keys())
    manifests = {}
    for timeframe in timeframes:
        frame = market_data.get(timeframe)
        if frame is None:
            manifests[timeframe] = {
                "status": "missing",
                "timeframe": timeframe,
                "row_count": 0,
                "canonical_candle_ids_tail": [],
                "raw_observation_ids_tail": [],
            }
        else:
            manifests[timeframe] = _timeframe_manifest(
                frame, timeframe, max_id_window
            )

    statuses = [item["status"] for item in manifests.values()]
    if statuses and all(status == "canonical" for status in statuses):
        overall_status = "complete"
    elif any(status in {"canonical", "partial"} for status in statuses):
        overall_status = "partial"
    elif not statuses or all(status == "missing" for status in statuses):
        overall_status = "missing"
    else:
        overall_status = "untracked"

    return {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "instrument_id": instrument_id,
        "status": overall_status,
        "timeframes": manifests,
    }
