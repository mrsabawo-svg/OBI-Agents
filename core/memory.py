"""Optimistic, verified GitHub Gist memory persistence for OBI."""
import copy
import hashlib
import json
import os
import threading
from typing import Callable, Optional

import requests

GIST_ID = os.environ.get("GIST_ID")
GH_TOKEN = os.environ.get("GIST_TOKEN")
HEADERS = {"Authorization": "token " + str(GH_TOKEN), "Accept": "application/vnd.github+json"}
FILENAME = "obi_memory.json"
META_KEY = "_memory_meta"
SCHEMA_VERSION = 1
MAX_TRANSACTIONS = 200
_LAST_KNOWN_GOOD = None
_READ_IS_STALE = True
_BASE_REVISION = None
_LOCK = threading.RLock()


class MemoryReadError(RuntimeError):
    """No trustworthy canonical snapshot is available."""


class MemoryConflictError(RuntimeError):
    """The canonical snapshot changed since it was loaded."""


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(data):
    return hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def _metadata(snapshot):
    meta = snapshot.get(META_KEY, {})
    if not isinstance(meta, dict):
        raise MemoryReadError("memory metadata must be an object")
    revision = meta.get("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise MemoryReadError("memory revision must be a non-negative integer")
    if meta.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise MemoryReadError("unsupported memory schema version")
    transactions = meta.get("transactions", [])
    if not isinstance(transactions, list):
        raise MemoryReadError("memory transaction ledger must be a list")
    for item in transactions:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("digest"), str):
            raise MemoryReadError("invalid memory transaction ledger entry")
    return {"schema_version": SCHEMA_VERSION, "revision": revision, "transactions": transactions}


def _remote_read():
    if not GIST_ID or not GH_TOKEN:
        raise MemoryReadError("GIST_ID and GIST_TOKEN must be configured")
    response = requests.get("https://api.github.com/gists/" + str(GIST_ID), headers=HEADERS, timeout=15)
    if response.status_code != 200:
        raise MemoryReadError("Gist read failed with HTTP " + str(response.status_code))
    try:
        info = response.json().get("files", {}).get(FILENAME)
        if not isinstance(info, dict):
            raise MemoryReadError("canonical memory file is missing")
        if info.get("truncated") is True:
            raise MemoryReadError("canonical memory file is truncated")
        content = info.get("content")
        if not isinstance(content, str) or not content.strip():
            raise MemoryReadError("canonical memory file is empty")
        snapshot = json.loads(content)
    except MemoryReadError:
        raise
    except Exception as exc:
        raise MemoryReadError("canonical memory could not be decoded: " + str(exc)) from exc
    if not isinstance(snapshot, dict):
        raise MemoryReadError("canonical memory root must be an object")
    return snapshot, _metadata(snapshot)


def load() -> dict:
    """Load fresh memory; stale last-known-good is read-only fallback only."""
    global _LAST_KNOWN_GOOD, _READ_IS_STALE, _BASE_REVISION
    with _LOCK:
        try:
            snapshot, meta = _remote_read()
            _LAST_KNOWN_GOOD = copy.deepcopy(snapshot)
            _BASE_REVISION = meta["revision"]
            _READ_IS_STALE = False
            return copy.deepcopy(snapshot)
        except Exception as exc:
            _READ_IS_STALE = True
            _BASE_REVISION = None
            if _LAST_KNOWN_GOOD is not None:
                print("[MEMORY] Remote read failed; returning read-only last-known-good: " + str(exc))
                return copy.deepcopy(_LAST_KNOWN_GOOD)
            raise MemoryReadError("memory unavailable; refusing an empty fallback: " + str(exc)) from exc


def load_with_status() -> tuple[dict, bool]:
    """Return a snapshot and its freshness atomically under the memory lock."""
    with _LOCK:
        snapshot = load()
        return snapshot, _READ_IS_STALE


def save(data: dict, transaction_id: Optional[str] = None) -> bool:
    """Revision-check, write, then read back exact content before success."""
    global _LAST_KNOWN_GOOD, _READ_IS_STALE, _BASE_REVISION
    with _LOCK:
        if _READ_IS_STALE or _BASE_REVISION is None:
            print("[MEMORY] Save blocked: a fresh canonical read is required")
            return False
        if not isinstance(data, dict):
            print("[MEMORY] Save blocked: memory root must be a dictionary")
            return False
        if transaction_id is not None and (not isinstance(transaction_id, str) or not transaction_id.strip()):
            print("[MEMORY] Save blocked: transaction_id must be a non-empty string")
            return False
        try:
            # The expected revision belongs to this exact snapshot. Do not use
            # the process-global revision as the concurrency token: another
            # caller may have loaded a newer snapshot in the meantime.
            snapshot_revision = _metadata(data)["revision"]
            remote, meta = _remote_read()
            if meta["revision"] != snapshot_revision:
                raise MemoryConflictError("stale snapshot revision: expected " + str(snapshot_revision) + ", found " + str(meta["revision"]))
            candidate = copy.deepcopy(data)
            candidate.pop(META_KEY, None)
            digest = _digest(candidate)
            transactions = list(meta["transactions"])
            if transaction_id is not None:
                previous = next((x for x in transactions if x["id"] == transaction_id), None)
                if previous:
                    if previous["digest"] != digest:
                        raise MemoryConflictError("transaction_id reused with a different payload")
                    _LAST_KNOWN_GOOD = copy.deepcopy(remote)
                    _BASE_REVISION = meta["revision"]
                    _READ_IS_STALE = False
                    return True
                transactions = (transactions + [{"id": transaction_id, "digest": digest}])[-MAX_TRANSACTIONS:]
            next_revision = meta["revision"] + 1
            candidate[META_KEY] = {"schema_version": SCHEMA_VERSION, "revision": next_revision, "transactions": transactions}
            body = json.dumps(candidate, indent=2, ensure_ascii=False)
            response = requests.patch(
                "https://api.github.com/gists/" + str(GIST_ID),
                headers=HEADERS,
                json={"files": {FILENAME: {"content": body}}},
                timeout=15,
            )
            if response.status_code != 200:
                raise RuntimeError("Gist write failed with HTTP " + str(response.status_code))
            verified, verified_meta = _remote_read()
            if verified_meta["revision"] != next_revision or verified != candidate:
                raise RuntimeError("read-after-write verification mismatch")
            _LAST_KNOWN_GOOD = copy.deepcopy(verified)
            _BASE_REVISION = next_revision
            _READ_IS_STALE = False
            print("[MEMORY] Save verified at revision " + str(next_revision))
            return True
        except Exception as exc:
            _READ_IS_STALE = True
            _BASE_REVISION = None
            print("[MEMORY] Save rejected or unverified: " + str(exc))
            return False


def transaction(mutator: Callable[[dict], Optional[dict]], transaction_id: str) -> bool:
    """Mutate a fresh snapshot once, identified by a caller-supplied idempotency key."""
    if not callable(mutator):
        raise TypeError("mutator must be callable")
    if not isinstance(transaction_id, str) or not transaction_id.strip():
        raise ValueError("transaction_id must be a non-empty string")
    with _LOCK:
        working = copy.deepcopy(load())
        existing_ids = {item["id"] for item in _metadata(working)["transactions"]}
        if transaction_id in existing_ids:
            # The transaction was committed previously. In particular, do not
            # rerun a non-idempotent mutator after an ambiguous read-back failure.
            return True
        result = mutator(working)
        candidate = working if result is None else result
        if not isinstance(candidate, dict):
            raise TypeError("mutator must return a dictionary or None")
        return save(candidate, transaction_id=transaction_id)
