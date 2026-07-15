"""
OBI Agents - Archive Agent
Logs every fired signal to Gist.
Builds the historical dataset that EdgeAgent and ScoreAgent learn from.
"""
import os
import json
import requests
from datetime import datetime
import pytz

SAST         = pytz.timezone("Africa/Johannesburg")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GIST_ID      = os.environ.get("GIST_ID")

ARCHIVE_GIST_FILE = "obi_archive.json"


class ArchiveAgent:
    def __init__(self):
        self.headers = {"Authorization": "token " + str(GITHUB_TOKEN)}

    def log(self, signal: dict) -> bool:
        try:
            archive = self._load_archive()
            entry   = self._build_entry(signal)
            archive.append(entry)
            self._save_archive(archive)
            print("[ARCHIVE] Signal logged. Total: " + str(len(archive)))
            return True
        except Exception as e:
            print("[ARCHIVE] Log error: " + str(e))
            return False

    def get_history(self, symbol: str = None, limit: int = 100) -> list:
        try:
            archive = self._load_archive()
            if symbol:
                archive = [s for s in archive if s.get("symbol") == symbol]
            return archive[-limit:]
        except Exception as e:
            print("[ARCHIVE] Get history error: " + str(e))
            return []

    def update_outcome(self, signal_id: str, outcome: str, pnl_pips: float = 0):
        try:
            archive = self._load_archive()
            for entry in archive:
                if entry.get("id") == signal_id:
                    entry["outcome"]   = outcome
                    entry["pnl_pips"]  = pnl_pips
                    entry["closed_at"] = datetime.now(SAST).strftime("%Y-%m-%d %H:%M SAST")
                    entry["status"]    = "CLOSED"
                    break
            self._save_archive(archive)
            print("[ARCHIVE] Outcome updated: " + signal_id + " -> " + outcome)
        except Exception as e:
            print("[ARCHIVE] Update error: " + str(e))

    def _build_entry(self, signal: dict) -> dict:
        trigger = signal.get("trigger", {})
        regime  = signal.get("regime", {})
        bias    = signal.get("bias", {})
        htf     = signal.get("htf", {})
        mtf     = signal.get("mtf", {})

        # Support both dataclass and dict for bias
        if hasattr(bias, "score"):
            bias_score   = bias.score
            bias_factors = bias.factors
        else:
            bias_score   = bias.get("score")
            bias_factors = bias.get("factors", [])

        # Support both dataclass and dict for trigger
        if hasattr(trigger, "direction"):
            direction  = trigger.direction
            grade      = trigger.grade
            entry      = trigger.entry
            sl         = trigger.sl
            tp1        = trigger.tp1
            tp2        = trigger.tp2
            tp3        = trigger.tp3
            rr         = trigger.rr
            tags       = trigger.tags
            confluence = trigger.confluence
        else:
            direction  = trigger.get("direction")
            grade      = trigger.get("grade")
            entry      = trigger.get("entry")
            sl         = trigger.get("sl")
            tp1        = trigger.get("tp1")
            tp2        = trigger.get("tp2")
            tp3        = trigger.get("tp3")
            rr         = trigger.get("rr")
            tags       = trigger.get("tags", [])
            confluence = trigger.get("confluence")

        return {
            "id":           signal.get("symbol", "") + "_" + datetime.now(SAST).strftime("%Y%m%d%H%M"),
            "symbol":       signal.get("symbol"),
            "timestamp":    datetime.now(SAST).strftime("%Y-%m-%d %H:%M SAST"),
            "direction":    direction,
            "grade":        grade,
            "entry":        entry,
            "sl":           sl,
            "tp1":          tp1,
            "tp2":          tp2,
            "tp3":          tp3,
            "rr":           rr,
            "tags":         tags,
            "confluence":   confluence,
            "regime":       regime.get("label"),
            "regime_conf":  regime.get("confidence"),
            "htf_bias":     htf.get("bias"),
            "htf_conf":     htf.get("confidence"),
            "bias_score":   bias_score,
            "bias_factors": bias_factors,
            "bos":          mtf.get("bos"),
            "sweep":        mtf.get("sweep"),
            "ob":           mtf.get("order_block"),
            "outcome":      "PENDING",
            "pnl_pips":     0,
            "status":       "OPEN",
            "closed_at":    None
        }

    def _load_archive(self) -> list:
        try:
            r = requests.get(
                "https://api.github.com/gists/" + str(GIST_ID),
                headers=self.headers,
                timeout=15
            )
            files = r.json().get("files", {})
            if ARCHIVE_GIST_FILE in files:
                content = files[ARCHIVE_GIST_FILE].get("content", "[]")
                return json.loads(content)
            return []
        except Exception as e:
            print("[ARCHIVE] Load error: " + str(e))
            return []

    def _save_archive(self, archive: list):
        requests.patch(
            "https://api.github.com/gists/" + str(GIST_ID),
            headers=self.headers,
            json={"files": {ARCHIVE_GIST_FILE: {"content": json.dumps(archive, indent=2)}}},
            timeout=15
        )
