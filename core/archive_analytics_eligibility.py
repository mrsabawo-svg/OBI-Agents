"""Shared eligibility boundary for trusted archive outcome analytics.

This module is intentionally conservative: records that cannot prove identity,
terminal lifecycle, market-data lineage, instrument verification, and decision
provenance are excluded rather than assigned a guessed result.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from core.decision_lineage import _sha256, verify_decision_lineage

ANALYTICS_ELIGIBILITY_SCHEMA_VERSION = "obi-archive-analytics-eligibility-v1"
LIFECYCLE_SCHEMA_VERSION = "obi-lifecycle-v1"
GRADE_SCHEMA_VERSION = "obi-composite-grade-v1"
TRIGGER_GRADE_SCHEMA_VERSION = "obi-trigger-grade-v1"
TERMINAL_OUTCOMES = {"TP3", "SL"}


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _structural_identity(record: dict) -> str | None:
    if not isinstance(record, dict):
        return None
    material = {
        "symbol": record.get("symbol"),
        "opened": record.get("opened") or record.get("timestamp"),
        "direction": record.get("direction"),
        "entry": record.get("entry"),
        "sl": record.get("sl"),
        "tp1": record.get("tp1"),
        "tp2": record.get("tp2"),
        "tp3": record.get("tp3"),
    }
    if any(value is None or value == "" for value in material.values()):
        return None
    return _canonical_hash(material)


def event_identity(record: dict) -> str | None:
    """Return issued event identity, or a deterministic legacy fallback."""
    event_id = record.get("id") if isinstance(record, dict) else None
    if isinstance(event_id, str) and event_id.strip():
        return "id:" + event_id.strip()
    structural = _structural_identity(record)
    return "legacy:" + structural if structural else None


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )


def _verified_data_lineage(record: dict) -> bool:
    lineage = record.get("data_lineage")
    if not isinstance(lineage, dict):
        return False
    if record.get("data_lineage_status") != "complete" or lineage.get("status") != "complete":
        return False
    if lineage.get("instrument_id") != record.get("symbol"):
        return False
    timeframes = lineage.get("timeframes")
    if not isinstance(timeframes, dict) or not timeframes:
        return False
    for timeframe, item in timeframes.items():
        if not isinstance(timeframe, str) or not isinstance(item, dict):
            return False
        if item.get("status") != "canonical":
            return False
        if item.get("instrument_verification_status") != "verified":
            return False
        if not isinstance(item.get("instrument_verification_id"), str) or not item["instrument_verification_id"].strip():
            return False
        if not _valid_sha256(item.get("instrument_metadata_sha256")):
            return False
        if not isinstance(item.get("broker_symbol"), str) or not item["broker_symbol"].strip():
            return False
        if not isinstance(item.get("broker"), str) or not item["broker"].strip():
            return False
        if not isinstance(item.get("account_fingerprint"), str) or not _valid_sha256(item["account_fingerprint"]):
            return False
        ids = item.get("canonical_candle_ids_tail")
        raw_ids = item.get("raw_observation_ids_tail")
        if not isinstance(ids, list) or not ids or not all(isinstance(x, str) and x for x in ids):
            return False
        if not isinstance(raw_ids, list) or not raw_ids or not all(isinstance(x, str) and x for x in raw_ids):
            return False
    return True


def _verified_decision_lineage(record: dict) -> bool:
    lineage = record.get("decision_lineage")
    if not isinstance(lineage, dict):
        return False
    if record.get("decision_lineage_status") != "complete":
        return False
    if record.get("decision_lineage_provenance_status") != "complete":
        return False
    if record.get("decision_lineage_identity_status") != "bound":
        return False
    if lineage.get("status") != "complete" or lineage.get("provenance_status") != "complete":
        return False
    if lineage.get("data_lineage_status") != "complete":
        return False
    if lineage.get("data_lineage_sha256") != _sha256(record.get("data_lineage")):
        return False
    if record.get("decision_lineage_sha256") != lineage.get("manifest_sha256"):
        return False
    identity = lineage.get("signal_identity")
    if not isinstance(identity, dict):
        return False
    if identity.get("signal_id") != record.get("id"):
        return False
    if lineage.get("instrument_id") != record.get("symbol"):
        return False
    if record.get("setup_identity_status") != "complete":
        return False
    if identity.get("setup_id") != record.get("setup_id"):
        return False
    if identity.get("setup_identity_version") != record.get("setup_identity_version"):
        return False
    try:
        return verify_decision_lineage(lineage)
    except Exception:
        return False


def _has_ambiguous_lifecycle(record: dict) -> bool:
    if record.get("lifecycle_event") == "AMBIGUOUS":
        return True
    if record.get("resolution_note") == "AMBIGUOUS_SAME_CANDLE_TARGET_AND_STOP":
        return True
    events = record.get("lifecycle_events")
    return isinstance(events, list) and any(
        isinstance(event, dict) and event.get("event") == "AMBIGUOUS"
        for event in events
    )


def eligibility_reasons(record: dict) -> list[str]:
    """Return all reasons a record cannot enter trusted terminal analytics."""
    reasons = []
    if not isinstance(record, dict):
        return ["invalid_record"]
    event_id = record.get("id")
    if not isinstance(event_id, str) or not event_id.strip():
        reasons.append("missing_issued_event_identity")
    if record.get("status") != "CLOSED":
        reasons.append("not_closed")
    terminal = record.get("terminal_outcome")
    if not isinstance(terminal, str) or terminal not in TERMINAL_OUTCOMES:
        reasons.append("non_terminal_or_unsupported_outcome")
    if record.get("outcome") != terminal:
        reasons.append("outcome_terminal_mismatch")
    if record.get("lifecycle_schema_version") != LIFECYCLE_SCHEMA_VERSION:
        reasons.append("unversioned_lifecycle_semantics")
    if _has_ambiguous_lifecycle(record):
        reasons.append("ambiguous_lifecycle")
    if not isinstance(record.get("grade_schema_version"), str) or not record.get("grade_schema_version").strip():
        reasons.append("unversioned_grade_semantics")
    if not _verified_data_lineage(record):
        reasons.append("incomplete_or_unverified_data_lineage")
    if not _verified_decision_lineage(record):
        reasons.append("incomplete_or_unverified_decision_provenance")
    if _structural_identity(record) is None:
        reasons.append("insufficient_structural_identity")
    return reasons


def _terminal_conflict(records: list[dict]) -> bool:
    if any(_has_ambiguous_lifecycle(record) for record in records):
        return True
    terminal_values = {
        record.get("terminal_outcome")
        for record in records
        if isinstance(record.get("terminal_outcome"), str)
        and record.get("terminal_outcome").strip()
    }
    if len(terminal_values) > 1:
        return True
    return any(
        isinstance(record.get("terminal_outcome"), str)
        and record.get("terminal_outcome") in TERMINAL_OUTCOMES
        and record.get("outcome") not in (None, record.get("terminal_outcome"))
        for record in records
    )


def select_eligible_events(archive: list[dict]) -> dict:
    """Deduplicate first, quarantine conflicts, then apply the shared gate.

    Duplicate lifecycle snapshots with the same event identity are collapsed.
    Conflicting structural identities or contradictory terminal outcomes are
    quarantined as a group. The returned snapshot digest is order-independent.
    """
    groups = defaultdict(list)
    unidentifiable = []
    for record in archive if isinstance(archive, list) else []:
        identity = event_identity(record)
        if identity is None:
            unidentifiable.append(record)
        else:
            groups[identity].append(record)

    eligible = []
    quarantined = []
    excluded = []
    duplicate_count = 0
    for identity in sorted(groups):
        records = groups[identity]
        duplicate_count += max(0, len(records) - 1)
        structural = {_structural_identity(record) for record in records}
        if None in structural or len(structural) > 1 or _terminal_conflict(records):
            quarantined.append({"identity": identity, "reason": "conflicting_duplicate_identity_or_terminal_outcome"})
            continue
        # Prefer a record that passes the complete gate; otherwise retain the
        # most informative deterministic representative for exclusion reasons.
        ranked = sorted(
            records,
            key=lambda item: (
                len(eligibility_reasons(item)),
                _canonical_hash(item),
            ),
        )
        representative = ranked[0]
        reasons = eligibility_reasons(representative)
        if reasons:
            excluded.append({"identity": identity, "reasons": reasons})
        else:
            eligible.append(representative)

    for record in unidentifiable:
        excluded.append({"identity": None, "reasons": ["insufficient_structural_identity"]})

    eligible.sort(key=lambda item: event_identity(item) or "")
    snapshot_material = [
        {
            "identity": event_identity(item),
            "terminal_outcome": item.get("terminal_outcome"),
            "lifecycle_schema_version": item.get("lifecycle_schema_version"),
            "data_lineage_sha256": item.get("decision_lineage", {}).get("data_lineage_sha256"),
            "decision_lineage_sha256": item.get("decision_lineage_sha256"),
            "setup_id": item.get("setup_id"),
            "symbol": item.get("symbol"),
            "regime": item.get("regime"),
            "tags": sorted(item.get("tags", [])) if isinstance(item.get("tags", []), list) else [],
            "factors": sorted(item.get("factors", [])) if isinstance(item.get("factors", []), list) else [],
            "grade": item.get("grade"),
            "grade_schema_version": item.get("grade_schema_version"),
            "obi_score": item.get("obi_score"),
        }
        for item in eligible
    ]
    return {
        "events": eligible,
        "quarantined": quarantined,
        "excluded": excluded,
        "duplicate_count": duplicate_count,
        "snapshot_sha256": _canonical_hash({
            "schema_version": ANALYTICS_ELIGIBILITY_SCHEMA_VERSION,
            "events": snapshot_material,
        }),
    }
