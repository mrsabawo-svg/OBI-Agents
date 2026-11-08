"""Evidence-based completeness assessment for a requested MT5 bar window."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


HISTORY_COMPLETENESS_SCHEMA_VERSION = "obi-history-completeness-v1"

TIMEFRAME_SECONDS = {
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
}

HARD_FAILURE_REASONS = {
    "EMPTY_HISTORY",
    "REQUESTED_COUNT_MISMATCH",
    "RAW_COUNT_MISMATCH",
    "INVALID_TIMESTAMP",
    "DUPLICATE_TIMESTAMP",
    "OUT_OF_ORDER",
    "TIMEFRAME_ALIGNMENT_ERROR",
    "FUTURE_BAR",
    "PAGINATION_SNAPSHOT_CHANGED",
}


def _timestamp(value: Any) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number <= 0 or not number.is_integer():
        return None
    return int(number)


def _utc_datetime(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("retrieved_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def assess_history_completeness(
    raw_rows: list[dict[str, Any]],
    timeframe: str,
    requested_count: int,
    fetch_metadata: dict[str, Any] | None = None,
    *,
    start_pos: int = 0,
    retrieved_at: datetime | str | None = None,
    session_model: str = "unknown",
) -> dict[str, Any]:
    """Assess the exact requested window; never infer unexplained market closures.

    Complete means the requested count was returned in a stable, aligned,
    ordered, duplicate-free window with no unexplained gaps. It does not claim
    that the broker has no older history outside this requested window.
    """
    if timeframe not in TIMEFRAME_SECONDS:
        raise ValueError("unsupported timeframe")
    if (
        isinstance(requested_count, bool)
        or not isinstance(requested_count, int)
        or requested_count < 1
    ):
        raise ValueError("requested_count must be a positive integer")
    if isinstance(start_pos, bool) or not isinstance(start_pos, int) or start_pos < 0:
        raise ValueError("start_pos must be a non-negative integer")
    if session_model not in {"unknown", "continuous_24_7"}:
        raise ValueError("unsupported session_model")
    if not isinstance(raw_rows, list):
        raise ValueError("raw_rows must be a list")

    fetch_metadata = fetch_metadata or {}
    interval = TIMEFRAME_SECONDS[timeframe]
    now = _utc_datetime(retrieved_at)
    current_bar_open = (int(now.timestamp()) // interval) * interval

    returned_count = fetch_metadata.get("returned_count", len(raw_rows))
    if isinstance(returned_count, bool) or not isinstance(returned_count, int):
        returned_count = -1

    reasons: list[str] = []
    timestamps: list[int | None] = [
        _timestamp(row.get("time")) if isinstance(row, dict) else None
        for row in raw_rows
    ]
    valid_timestamps = [value for value in timestamps if value is not None]
    invalid_timestamp_count = len(timestamps) - len(valid_timestamps)

    if not raw_rows:
        reasons.append("EMPTY_HISTORY")
    if returned_count != requested_count:
        reasons.append("REQUESTED_COUNT_MISMATCH")
    if returned_count != len(raw_rows):
        reasons.append("RAW_COUNT_MISMATCH")
    if invalid_timestamp_count:
        reasons.append("INVALID_TIMESTAMP")

    counts: dict[int, int] = {}
    for value in valid_timestamps:
        counts[value] = counts.get(value, 0) + 1
    duplicate_timestamp_count = sum(1 for count in counts.values() if count > 1)
    duplicate_observation_count = sum(count - 1 for count in counts.values() if count > 1)
    duplicate_group_count = duplicate_timestamp_count
    if duplicate_observation_count:
        reasons.append("DUPLICATE_TIMESTAMP")

    out_of_order_count = 0
    alignment_error_count = 0
    future_bar_count = 0
    forming_indexes: list[int] = []
    closed_count = 0
    for index, value in enumerate(timestamps):
        if value is None:
            continue
        if value % interval != 0:
            alignment_error_count += 1
        if value > current_bar_open:
            future_bar_count += 1
        if start_pos == 0 and value == current_bar_open:
            forming_indexes.append(index)
        else:
            closed_count += 1
        if index > 0 and timestamps[index - 1] is not None:
            if value < timestamps[index - 1]:
                out_of_order_count += 1
    if out_of_order_count:
        reasons.append("OUT_OF_ORDER")
    if alignment_error_count:
        reasons.append("TIMEFRAME_ALIGNMENT_ERROR")
    if future_bar_count:
        reasons.append("FUTURE_BAR")

    gaps: list[dict[str, int]] = []
    missing_interval_count = 0
    for left, right in zip(timestamps, timestamps[1:]):
        if left is None or right is None or right <= left:
            continue
        difference = right - left
        if difference > interval and difference % interval == 0:
            missing = difference // interval - 1
            missing_interval_count += missing
            gaps.append({
                "after_timestamp": left,
                "before_timestamp": right,
                "missing_intervals": missing,
            })
        elif difference > interval and difference % interval != 0:
            if "TIMEFRAME_ALIGNMENT_ERROR" not in reasons:
                reasons.append("TIMEFRAME_ALIGNMENT_ERROR")

    snapshot_stable = fetch_metadata.get("snapshot_stable", True)
    snapshot_check_performed = bool(fetch_metadata.get("snapshot_check_performed", False))
    if snapshot_stable is False:
        reasons.append("PAGINATION_SNAPSHOT_CHANGED")
    elif snapshot_check_performed and snapshot_stable is not True:
        reasons.append("PAGINATION_SNAPSHOT_UNVERIFIABLE")
    elif fetch_metadata.get("page_count", 1) > 1 and not snapshot_check_performed:
        reasons.append("PAGINATION_SNAPSHOT_UNVERIFIABLE")

    first_timestamp = min(valid_timestamps) if valid_timestamps else None
    last_timestamp = max(valid_timestamps) if valid_timestamps else None
    latest_timestamp = last_timestamp

    stale_latest_bar = False
    freshness_status = "not_applicable_historical" if start_pos > 0 else "fresh"
    if start_pos == 0 and latest_timestamp is not None:
        expected_latest_closed_open = current_bar_open - interval
        stale_latest_bar = latest_timestamp < expected_latest_closed_open
        if stale_latest_bar:
            freshness_status = "stale_context_unknown"
            if session_model == "continuous_24_7":
                reasons.append("STALE_DATA")
            else:
                reasons.append("FRESHNESS_CONTEXT_UNKNOWN")

    if missing_interval_count:
        if session_model == "continuous_24_7":
            reasons.append("MISSING_BARS")
        else:
            reasons.append("GAP_CONTEXT_UNKNOWN")

    hard_failure = any(
        reason in HARD_FAILURE_REASONS or reason in {"MISSING_BARS", "STALE_DATA"}
        for reason in reasons
    )
    unverifiable = any(
        reason in {
            "GAP_CONTEXT_UNKNOWN",
            "FRESHNESS_CONTEXT_UNKNOWN",
            "PAGINATION_SNAPSHOT_UNVERIFIABLE",
        }
        for reason in reasons
    )
    if hard_failure:
        status = "incomplete"
    elif unverifiable:
        status = "unverifiable"
    else:
        status = "complete"

    def iso(value: int | None) -> str | None:
        if value is None:
            return None
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()

    return {
        "schema_version": HISTORY_COMPLETENESS_SCHEMA_VERSION,
        "status": status,
        "coverage_scope": "requested_window",
        "session_model": session_model,
        "timeframe": timeframe,
        "interval_seconds": interval,
        "requested_start_pos": start_pos,
        "requested_count": requested_count,
        "returned_count": returned_count,
        "valid_timestamp_count": len(valid_timestamps),
        "invalid_timestamp_count": invalid_timestamp_count,
        "duplicate_timestamp_count": duplicate_timestamp_count,
        "duplicate_observation_count": duplicate_observation_count,
        "duplicate_group_count": duplicate_group_count,
        "out_of_order_count": out_of_order_count,
        "timeframe_alignment_error_count": alignment_error_count,
        "missing_interval_count": missing_interval_count,
        "gap_segment_count": len(gaps),
        "gaps": gaps[:25],
        "forming_count": len(forming_indexes),
        "forming_observation_indexes": forming_indexes,
        "closed_count": closed_count,
        "future_bar_count": future_bar_count,
        "latest_bar_stale": stale_latest_bar,
        "freshness_status": freshness_status,
        "first_bar_open_time": iso(first_timestamp),
        "last_bar_open_time": iso(last_timestamp),
        "latest_bar_open_time": iso(latest_timestamp),
        "retrieved_at": now.isoformat(),
        "snapshot_stable": snapshot_stable,
        "snapshot_check_performed": snapshot_check_performed,
        "pagination": {
            "page_count": fetch_metadata.get("page_count", 1),
            "page_sizes": fetch_metadata.get("page_sizes", [len(raw_rows)]),
            "page_start_positions": fetch_metadata.get("page_start_positions", [start_pos]),
        },
        "reasons": list(dict.fromkeys(reasons)),
    }
