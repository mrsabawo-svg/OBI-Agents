"""Validated instrument-registry records for OBI market-data acquisition.

A family catalogue entry is not proof that a broker symbol exists in an account.
"""
from typing import Any

FAMILY_IDS = frozenset({
    "FX_VOL", "SFX_VOL", "PAIN", "GAIN", "FLIP", "SWITCH", "BREAK",
    "TREND", "PLUS", "FIBO", "QUAD", "MAX_PAIN", "MAX_GAIN",
})
ACCOUNT_ENVIRONMENTS = frozenset({"demo", "live", "contest", "unknown"})
AVAILABILITY_STATES = frozenset({
    "unverified", "visible_in_market_watch", "selectable", "data_verified",
    "disabled", "unavailable",
})
VERIFICATION_STATES = frozenset({
    "unverified", "partially_verified", "verified", "rejected", "stale",
})
SOURCE_TYPES = frozenset({
    "broker_ticks", "broker_bars", "third_party", "simulated_test_only", "unknown",
})

REQUIRED_FIELDS = (
    "instrument_id", "family_id", "display_name", "broker",
    "account_environment", "platform", "availability_status",
    "source_type", "verification_status", "registry_version",
)


class RegistryValidationError(ValueError):
    """Raised when a registry record violates the v0.1 contract."""


def validate_instrument_record(record: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a registry record without mutating it.

    Unknown broker-specific values remain explicit; broker_symbol may be None
    until observed from the connected account.
    """
    if not isinstance(record, dict):
        raise RegistryValidationError("record must be an object")

    missing = [field for field in REQUIRED_FIELDS if field not in record]
    if missing:
        raise RegistryValidationError("missing required fields: " + ", ".join(missing))

    for field in ("instrument_id", "family_id", "display_name", "broker",
                  "platform", "registry_version"):
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RegistryValidationError(f"{field} must be a non-empty string")

    if record["family_id"] not in FAMILY_IDS:
        raise RegistryValidationError("family_id is not in the approved family catalogue")
    if record["account_environment"] not in ACCOUNT_ENVIRONMENTS:
        raise RegistryValidationError("invalid account_environment")
    if record["availability_status"] not in AVAILABILITY_STATES:
        raise RegistryValidationError("invalid availability_status")
    if record["verification_status"] not in VERIFICATION_STATES:
        raise RegistryValidationError("invalid verification_status")
    if record["source_type"] not in SOURCE_TYPES:
        raise RegistryValidationError("invalid source_type")

    broker_symbol = record.get("broker_symbol")
    if broker_symbol is not None and (
        not isinstance(broker_symbol, str) or not broker_symbol.strip()
    ):
        raise RegistryValidationError("broker_symbol must be null or a non-empty string")

    timeframes = record.get("supported_timeframes", [])
    if not isinstance(timeframes, list) or any(
        not isinstance(tf, str) or not tf.strip() for tf in timeframes
    ):
        raise RegistryValidationError("supported_timeframes must be a list of non-empty strings")

    if record["verification_status"] == "verified":
        if not broker_symbol:
            raise RegistryValidationError("verified records require an exact broker_symbol")
        if record["availability_status"] != "data_verified":
            raise RegistryValidationError("verified records require data_verified availability")
        if not timeframes:
            raise RegistryValidationError("verified records require verified timeframes")
        if record["account_environment"] == "unknown":
            raise RegistryValidationError("verified records require a known account environment")
        evidence_refs = record.get("evidence_refs")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            raise RegistryValidationError("verified records require evidence_refs")
        if not record.get("last_verified_at"):
            raise RegistryValidationError("verified records require last_verified_at")

    fingerprint = record.get("account_fingerprint")
    if fingerprint is not None and (
        not isinstance(fingerprint, str) or not fingerprint.strip()
    ):
        raise RegistryValidationError("account_fingerprint must be null or non-empty")
    return record
