"""
OBI Agents - Archive Agent
Canonical signal archive adapter.

The archive is stored inside core.memory under the `_archive` key.
This module MUST NOT maintain a second archive file or Gist.
"""
from datetime import datetime
from copy import deepcopy
import pytz

from core.memory import load as load_memory, save as save_memory
from core.signal_identity import SETUP_IDENTITY_VERSION, build_setup_identity
from core.decision_lineage import (
    DECISION_LINEAGE_SCHEMA_VERSION,
    verify_decision_lineage,
)

SAST = pytz.timezone("Africa/Johannesburg")


class ArchiveAgent:
    """Read/write signal history through the canonical memory store."""

    def log(self, signal: dict) -> bool:
        try:
            memory = load_memory() or {}
            archive = memory.setdefault("_archive", [])

            entry = self._build_entry(signal)

            # Prevent duplicate writes for the same signal id.
            if any(item.get("id") == entry.get("id") for item in archive):
                print("[ARCHIVE] Duplicate signal ignored: " + str(entry.get("id")))
                return False

            archive.append(entry)
            if save_memory(memory) is not True:
                print("[ARCHIVE] Persistence failed; signal was not accepted as archived")
                return False
            print("[ARCHIVE] Signal logged. Total: " + str(len(archive)))
            return True
        except Exception as e:
            print("[ARCHIVE] Log error: " + str(e))
            return False

    def get_history(self, symbol: str = None, limit: int = 100) -> list:
        try:
            memory = load_memory() or {}
            archive = memory.get("_archive", [])
            if symbol:
                archive = [s for s in archive if s.get("symbol") == symbol]
            return archive[-limit:]
        except Exception as e:
            print("[ARCHIVE] Get history error: " + str(e))
            return []

    def _build_entry(self, signal: dict) -> dict:
        trigger = signal.get("trigger", {})
        regime = signal.get("regime", {})
        bias = signal.get("bias", {})
        htf = signal.get("htf", {})
        mtf = signal.get("mtf", {})

        if hasattr(bias, "score"):
            bias_score = bias.score
            bias_factors = bias.factors
        else:
            bias_score = bias.get("score")
            bias_factors = bias.get("factors", [])

        score = signal.get("score") or {}
        final_grade = score.grade if hasattr(score, "grade") else score.get("grade") if isinstance(score, dict) else None
        obi_score = (
            score.confidence if hasattr(score, "confidence")
            else score.get("confidence") if isinstance(score, dict)
            else None
        )

        if hasattr(trigger, "direction"):
            direction = trigger.direction
            grade = final_grade or trigger.grade
            trigger_grade = trigger.grade
            entry = trigger.entry
            sl = trigger.sl
            tp1 = trigger.tp1
            tp2 = trigger.tp2
            tp3 = trigger.tp3
            rr = trigger.rr
            tags = trigger.tags
            confluence = trigger.confluence
        else:
            direction = trigger.get("direction")
            grade = final_grade or trigger.get("grade")
            trigger_grade = trigger.get("grade")
            entry = trigger.get("entry")
            sl = trigger.get("sl")
            tp1 = trigger.get("tp1")
            tp2 = trigger.get("tp2")
            tp3 = trigger.get("tp3")
            rr = trigger.get("rr")
            tags = trigger.get("tags", [])
            confluence = trigger.get("confluence")

        timestamp = datetime.now(SAST).strftime("%Y-%m-%d %H:%M SAST")
        signal_id = signal.get("id") or (
            str(signal.get("symbol", "")) + "_" + datetime.now(SAST).strftime("%Y%m%d%H%M%S")
        )
        data_lineage = signal.get("data_lineage")
        if not isinstance(data_lineage, dict):
            data_lineage = {
                "schema_version": "obi-data-lineage-v3",
                "instrument_id": signal.get("symbol"),
                "status": "unavailable",
                "timeframes": {},
            }
        else:
            data_lineage = deepcopy(data_lineage)

        try:
            setup_id = build_setup_identity(signal)
            setup_identity_version = SETUP_IDENTITY_VERSION
            setup_identity_status = "complete"
        except (TypeError, ValueError) as exc:
            # Preserve compatibility with minimal/legacy archive callers without
            # inventing an identity from incomplete setup fields.
            print("[ARCHIVE] Setup identity unavailable: " + str(exc))
            setup_id = None
            setup_identity_version = None
            setup_identity_status = "unavailable"

        decision_lineage = signal.get("decision_lineage")
        lineage_identity_status = "unbound"
        identity_matches = True
        if isinstance(decision_lineage, dict) and decision_lineage.get("signal_identity") is not None:
            bound_identity = decision_lineage.get("signal_identity")
            identity_matches = (
                isinstance(bound_identity, dict)
                and bound_identity.get("signal_id") == signal_id
                and bound_identity.get("setup_id") == setup_id
                and bound_identity.get("setup_identity_version") == setup_identity_version
                and decision_lineage.get("instrument_id") == signal.get("symbol")
            )
            lineage_identity_status = "bound" if identity_matches else "mismatch"

        if (
            isinstance(decision_lineage, dict)
            and decision_lineage.get("status") == "complete"
            and verify_decision_lineage(decision_lineage)
            and identity_matches
        ):
            decision_lineage = deepcopy(decision_lineage)
            if decision_lineage.get("signal_identity") is not None:
                lineage_identity_status = "bound"
        else:
            decision_lineage = {
                "schema_version": DECISION_LINEAGE_SCHEMA_VERSION,
                "status": "unavailable",
                "agent_order": [],
                "agent_specs": {},
                "agent_traces": {},
                "manifest_sha256": None,
            }
            if lineage_identity_status == "mismatch":
                lineage_identity_status = "mismatch"
            else:
                lineage_identity_status = "unavailable"

        return {
            "id": signal_id,
            "setup_identity_version": setup_identity_version,
            "setup_identity_status": setup_identity_status,
            "setup_id": setup_id,
            "symbol": signal.get("symbol"),
            "timestamp": timestamp,
            "opened": timestamp,
            "direction": direction,
            "grade": grade,
            "trigger_grade": trigger_grade,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "rr": rr,
            "tags": tags,
            "confluence": confluence,
            "regime": regime.get("label"),
            "regime_conf": regime.get("confidence"),
            "htf_bias": htf.get("bias"),
            "htf_conf": htf.get("confidence"),
            "bias_score": bias_score,
            # Canonical analytics schema consumed by FactorAgent.
            "factors": list(bias_factors or []),
            "bias_factors": list(bias_factors or []),  # compatibility alias
            "obi_score": obi_score,
            "bos": mtf.get("bos"),
            "sweep": mtf.get("sweep"),
            "ob": mtf.get("order_block"),
            "outcome": "PENDING",
            "pnl_pips": 0,
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
            "terminal_outcome": None,
            "terminal_pnl_pips": None,
            "realized_pnl_pips": None,
            "pnl_basis": None,
            "milestones": [],
            "lifecycle_last_candle": None,
            "lifecycle_events": [],
            "status": "OPEN",
            "closed_at": None,
            "closed": None,
            "data_lineage": data_lineage,
            "data_lineage_status": data_lineage.get("status", "unavailable"),
            "decision_lineage": decision_lineage,
            "decision_lineage_status": decision_lineage.get("status", "unavailable"),
            "decision_lineage_identity_status": lineage_identity_status,
            "decision_lineage_provenance_status": decision_lineage.get("provenance_status", "unavailable"),
            "decision_lineage_sha256": decision_lineage.get("manifest_sha256"),
        }
