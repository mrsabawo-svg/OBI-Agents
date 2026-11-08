from datetime import datetime, timezone

import pytest

from core.history_completeness import assess_history_completeness


NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def rows(*timestamps):
    return [{"time": value} for value in timestamps]


def fetch(count, *, stable=True, page_sizes=None, starts=None):
    return {
        "requested_count": count,
        "returned_count": count,
        "page_count": len(page_sizes or [count]),
        "page_sizes": page_sizes or [count],
        "page_start_positions": starts or [0],
        "snapshot_stable": stable,
        "snapshot_check_performed": len(page_sizes or [count]) > 1,
    }


def test_contiguous_requested_window_is_complete_and_marks_current_bar_forming():
    report = assess_history_completeness(
        rows(1794138600, 1794138900, 1794139200),
        "5m",
        3,
        fetch(3),
        retrieved_at=NOW,
    )
    assert report["status"] == "complete"
    assert report["requested_count"] == report["returned_count"] == 3
    assert report["forming_count"] == 1
    assert report["forming_observation_indexes"] == [2]
    assert report["closed_count"] == 2
    assert report["coverage_scope"] == "requested_window"


def test_short_history_is_incomplete_not_silently_accepted():
    report = assess_history_completeness(
        rows(1794138600, 1794138900),
        "5m",
        3,
        {"returned_count": 2, "snapshot_stable": True},
        retrieved_at=NOW,
    )
    assert report["status"] == "incomplete"
    assert "REQUESTED_COUNT_MISMATCH" in report["reasons"]


def test_unexplained_gap_is_unverifiable_without_a_session_model():
    report = assess_history_completeness(
        rows(1794138600, 1794139200),
        "5m",
        2,
        fetch(2),
        start_pos=1,
        retrieved_at=NOW,
    )
    assert report["status"] == "unverifiable"
    assert report["missing_interval_count"] == 1
    assert "GAP_CONTEXT_UNKNOWN" in report["reasons"]


def test_gap_is_incomplete_for_verified_continuous_market():
    report = assess_history_completeness(
        rows(1794138600, 1794139200),
        "5m",
        2,
        fetch(2),
        start_pos=1,
        retrieved_at=NOW,
        session_model="continuous_24_7",
    )
    assert report["status"] == "incomplete"
    assert "MISSING_BARS" in report["reasons"]


def test_duplicate_timestamp_is_incomplete():
    report = assess_history_completeness(
        rows(1794138600, 1794138600, 1794138900),
        "5m",
        3,
        fetch(3),
        start_pos=1,
        retrieved_at=NOW,
    )
    assert report["status"] == "incomplete"
    assert report["duplicate_timestamp_count"] == 2
    assert report["duplicate_group_count"] == 1


def test_out_of_order_timestamps_are_incomplete():
    report = assess_history_completeness(
        rows(1794138900, 1794138600, 1794139200),
        "5m",
        3,
        fetch(3),
        start_pos=1,
        retrieved_at=NOW,
    )
    assert report["status"] == "incomplete"
    assert report["out_of_order_count"] == 1


def test_timeframe_misalignment_is_incomplete():
    report = assess_history_completeness(
        rows(1794132301, 1794132601),
        "5m",
        2,
        fetch(2),
        start_pos=1,
        retrieved_at=NOW,
    )
    assert report["status"] == "incomplete"
    assert report["timeframe_alignment_error_count"] == 2


def test_future_bars_are_incomplete():
    report = assess_history_completeness(
        rows(1794139200, 1794139500),
        "5m",
        2,
        fetch(2),
        start_pos=1,
        retrieved_at=NOW,
    )
    assert report["status"] == "incomplete"
    assert report["future_bar_count"] == 1


def test_stale_latest_window_is_unverifiable_without_session_evidence():
    report = assess_history_completeness(
        rows(1794138300, 1794138600),
        "5m",
        2,
        fetch(2),
        retrieved_at=NOW,
    )
    assert report["status"] == "unverifiable"
    assert report["latest_bar_stale"] is True
    assert "FRESHNESS_CONTEXT_UNKNOWN" in report["reasons"]


def test_stale_latest_window_is_incomplete_for_continuous_market():
    report = assess_history_completeness(
        rows(1794138300, 1794138600),
        "5m",
        2,
        fetch(2),
        retrieved_at=NOW,
        session_model="continuous_24_7",
    )
    assert report["status"] == "incomplete"
    assert "STALE_DATA" in report["reasons"]


def test_pagination_snapshot_drift_is_incomplete():
    report = assess_history_completeness(
        rows(1794138600, 1794138900, 1794139200),
        "5m",
        3,
        fetch(3, stable=False, page_sizes=[2, 1], starts=[0, 2]),
        retrieved_at=NOW,
    )
    assert report["status"] == "incomplete"
    assert "PAGINATION_SNAPSHOT_CHANGED" in report["reasons"]


def test_historical_window_does_not_apply_latest_bar_freshness_check():
    report = assess_history_completeness(
        rows(1794120000, 1794120300),
        "5m",
        2,
        fetch(2),
        start_pos=500,
        retrieved_at=NOW,
    )
    assert report["status"] == "complete"
    assert report["freshness_status"] == "not_applicable_historical"


@pytest.mark.parametrize("timeframe", ["3m", "1d", ""])
def test_unsupported_timeframe_is_rejected(timeframe):
    with pytest.raises(ValueError, match="unsupported"):
        assess_history_completeness([], timeframe, 1, retrieved_at=NOW)
