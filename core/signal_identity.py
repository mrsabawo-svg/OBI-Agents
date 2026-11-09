"""Versioned identities for OBI signal events and material trade setups."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any
import uuid


SETUP_IDENTITY_VERSION = "obi-setup-identity-v1"


def _field(trigger: Any, name: str):
    if isinstance(trigger, dict):
        return trigger.get(name)
    return getattr(trigger, name, None)


def _decimal_label(value: Any, field_name: str, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError(field_name + " is required for setup identity")
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(field_name + " must be a finite number")
    if not number.is_finite():
        raise ValueError(field_name + " must be a finite number")
    if number == 0:
        return "0"
    return format(number.normalize(), "f")


def build_setup_identity(payload: dict) -> str:
    """Hash only material setup fields; signal IDs and timestamps are excluded."""
    if not isinstance(payload, dict):
        raise TypeError("signal payload must be a dictionary")

    symbol = payload.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol is required for setup identity")

    trigger = payload.get("trigger")
    direction = _field(trigger, "direction")
    if not isinstance(direction, str) or not direction.strip():
        raise ValueError("direction is required for setup identity")

    tags = _field(trigger, "tags") or []
    if isinstance(tags, str) or not isinstance(tags, (list, tuple, set)):
        raise ValueError("tags must be a sequence of strings")
    normalized_tags = []
    for tag in tags:
        if not isinstance(tag, str) or not tag.strip():
            raise ValueError("setup tags must be non-empty strings")
        normalized_tags.append(tag.strip())
    normalized_tags = sorted(set(normalized_tags))

    regime = payload.get("regime") or {}
    if not isinstance(regime, dict):
        regime = {}
    regime_label = regime.get("label")
    if regime_label is not None and not isinstance(regime_label, str):
        raise ValueError("regime label must be a string")

    material = {
        "identity_version": SETUP_IDENTITY_VERSION,
        "symbol": symbol.strip().upper(),
        "direction": direction.strip().upper(),
        "entry": _decimal_label(_field(trigger, "entry"), "entry", required=True),
        "sl": _decimal_label(_field(trigger, "sl"), "sl", required=True),
        "tp1": _decimal_label(_field(trigger, "tp1"), "tp1", required=True),
        "tp2": _decimal_label(_field(trigger, "tp2"), "tp2", required=True),
        "tp3": _decimal_label(_field(trigger, "tp3"), "tp3", required=True),
        "tags": normalized_tags,
        "regime": regime_label.strip().upper() if regime_label else None,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()



def create_signal_event_id(symbol: str) -> str:
    """Create the unique event identity for one fired signal."""
    normalized_symbol = str(symbol).strip().upper()
    if not normalized_symbol:
        raise ValueError("symbol must be a non-empty string")
    from core.utils import now_sast

    stamp = now_sast().strftime("%Y%m%d%H%M%S")
    return normalized_symbol + "_" + stamp + "_" + uuid.uuid4().hex


def create_signal_identity(symbol: str, trigger: Any, regime: dict | None = None) -> dict:
    """Issue the canonical event and setup identity bundle for a new signal."""
    payload = {"symbol": symbol, "trigger": trigger, "regime": regime or {}}
    return {
        "signal_id": create_signal_event_id(symbol),
        "setup_id": build_setup_identity(payload),
        "setup_identity_version": SETUP_IDENTITY_VERSION,
    }


def resolve_setup_identity(payload: dict) -> str:
    """Resolve a candidate setup identity through the canonical identity module.

    Existing issued identity is reused when valid; older callers without an
    issued bundle are normalized through the same versioned algorithm.
    """
    if not isinstance(payload, dict):
        raise TypeError("signal payload must be a dictionary")
    identity = payload.get("signal_identity")
    if isinstance(identity, dict):
        setup_id = identity.get("setup_id")
        version = identity.get("setup_identity_version")
        if (
            version == SETUP_IDENTITY_VERSION
            and isinstance(setup_id, str)
            and len(setup_id) == 64
            and all(character in "0123456789abcdef" for character in setup_id)
        ):
            return setup_id
    return build_setup_identity(payload)
