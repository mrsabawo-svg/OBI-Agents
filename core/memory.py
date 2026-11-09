"""
OBI Agents — Memory Core
Reads/writes agent memory to GitHub Gist.
"""
import copy
import json
import os
import threading

import requests

GIST_ID = os.environ.get("GIST_ID")
GH_TOKEN = os.environ.get("GIST_TOKEN")
HEADERS = {"Authorization": "token " + str(GH_TOKEN)}
FILENAME = "obi_memory.json"

_LAST_KNOWN_GOOD = None
_READ_IS_STALE = True
_LOCK = threading.RLock()


class MemoryReadError(RuntimeError):
    """Raised when no trustworthy memory snapshot can be read."""


def load() -> dict:
    """
    Load the canonical memory snapshot.

    A failed remote read may return an in-process last-known-good snapshot for
    read-only use, but it marks that snapshot stale. save() refuses writes
    until a fresh remote read succeeds, preventing stale/empty overwrites.
    """
    global _LAST_KNOWN_GOOD, _READ_IS_STALE
    with _LOCK:
        try:
            if not GIST_ID or not GH_TOKEN:
                raise MemoryReadError("GIST_ID and GIST_TOKEN must be configured")
            response = requests.get(
                "https://api.github.com/gists/" + str(GIST_ID),
                headers=HEADERS,
                timeout=15,
            )
            if response.status_code != 200:
                raise MemoryReadError(
                    "Gist read failed with HTTP " + str(response.status_code)
                )
            files = response.json().get("files", {})
            if FILENAME not in files:
                raise MemoryReadError("canonical memory file is missing from the Gist")
            content = files[FILENAME].get("content")
            if not isinstance(content, str) or not content.strip():
                raise MemoryReadError("canonical memory file is empty")
            memory = json.loads(content)
            if not isinstance(memory, dict):
                raise MemoryReadError("canonical memory root must be a JSON object")

            _LAST_KNOWN_GOOD = copy.deepcopy(memory)
            _READ_IS_STALE = False
            return copy.deepcopy(memory)
        except Exception as exc:
            _READ_IS_STALE = True
            if _LAST_KNOWN_GOOD is not None:
                print("[MEMORY] Remote read failed; using read-only last-known-good snapshot: " + str(exc))
                return copy.deepcopy(_LAST_KNOWN_GOOD)
            raise MemoryReadError(
                "memory unavailable; refusing an empty fallback: " + str(exc)
            ) from exc


def save(data: dict) -> bool:
    """Persist memory only after a fresh successful read in this process."""
    global _LAST_KNOWN_GOOD, _READ_IS_STALE
    with _LOCK:
        if _READ_IS_STALE:
            print("[MEMORY] Save blocked: no fresh trusted read is available")
            return False
        if not isinstance(data, dict):
            print("[MEMORY] Save blocked: memory root must be a dictionary")
            return False
        try:
            payload = {"files": {FILENAME: {"content": json.dumps(data, indent=2)}}}
            response = requests.patch(
                "https://api.github.com/gists/" + str(GIST_ID),
                headers=HEADERS,
                json=payload,
                timeout=15,
            )
            if response.status_code == 200:
                _LAST_KNOWN_GOOD = copy.deepcopy(data)
                _READ_IS_STALE = False
                print("[MEMORY] Saved successfully")
                return True
            _READ_IS_STALE = True
            print(
                "[MEMORY] Save failed - status "
                + str(response.status_code)
                + "; refusing to treat the write as durable"
            )
            return False
        except Exception as exc:
            _READ_IS_STALE = True
            print("[MEMORY] Save failed: " + str(exc))
            return False
