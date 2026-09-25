"""
OBI Agents - Archive Agent
Canonical signal archive adapter.

The archive is stored inside core.memory under the `_archive` key.
This module MUST NOT maintain a second archive file or Gist.
"""
from datetime import datetime
import pytz

from core.memory import load as load_memory, save as save_memory
from agents.signal_reviewer import SignalReviewer

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
                return True

            archive.append(entry)
            save_memory(memory)
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

    # Lifecycle authority invariant:
    # LifecycleAgent is the sole component permitted to close existing
    # archive records. ArchiveAgent creates records; it does not close them.

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

        score = signal.get("score", {})
        final_grade = score.grade if hasattr(score, "grade") else score.get("grade") if isinstance(score, dict) else None

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

        return {
            "id": signal_id,
            "setup_id": SignalReviewer.setup_identity(signal),
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
            "bias_factors": bias_factors,
            "bos": mtf.get("bos"),
            "sweep": mtf.get("sweep"),
            "ob": mtf.get("order_block"),
            "outcome": "PENDING",
            # Lifecycle milestone state is part of the canonical archive schema.
            # Initialize all flags so records have a stable shape before any
            # TP1/TP2/TP3 events occur.
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
            "pnl_pips": 0,
            "status": "OPEN",
            "closed_at": None,
            "closed": None,
        }
