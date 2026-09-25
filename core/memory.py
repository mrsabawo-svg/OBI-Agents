"""
OBI Agents — Memory Core
Reads/writes agent memory to GitHub Gist.

OTMP (OBI Transactional Memory Protocol) provides application-level
serialization, revision checks, transaction identity and idempotent
mutation support around GitHub Gist. GitHub Gist itself does not provide
remote compare-and-swap semantics, so this module must not claim
database-grade atomicity.
"""
import os
import json
import uuid
import requests
from copy import deepcopy

GIST_ID = os.environ.get("GIST_ID")
GH_TOKEN = os.environ.get("GIST_TOKEN")
print("[MEMORY] Initialized")

HEADERS = {"Authorization": "token " + str(GH_TOKEN)}
FILENAME = "obi_memory.json"
META_KEY = "_memory_meta"
REVISION_KEY = "revision"
TRANSACTIONS_KEY = "transactions"
TRANSACTION_HISTORY_LIMIT = 200


def _get_remote() -> dict:
    r = requests.get(
        "https://api.github.com/gists/" + str(GIST_ID),
        headers=HEADERS,
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError("Gist load failed - status " + str(r.status_code))
    files = r.json().get("files", {})
    content = files.get(FILENAME, {}).get("content", "{}")
    return json.loads(content)


def load() -> dict:
    try:
        return _get_remote()
    except Exception as e:
        print("[MEMORY] Load failed: " + str(e))
        return {}


def _revision(data: dict) -> int:
    meta = data.get(META_KEY, {})
    try:
        return int(meta.get(REVISION_KEY, 0))
    except (TypeError, ValueError):
        return 0


def _ensure_meta(data: dict) -> dict:
    meta = data.setdefault(META_KEY, {})
    meta.setdefault(REVISION_KEY, 0)
    meta.setdefault(TRANSACTIONS_KEY, [])
    return data


def save(data: dict, *, expected_revision: int = None,
         transaction_id: str = None) -> bool:
    """
    Persist one memory snapshot.

    expected_revision detects stale snapshots among cooperating OBI writers.
    transaction_id makes a retry of the same transaction idempotent.

    This is not a remote CAS: the Gist PATCH operation itself is not
    conditionally committed by GitHub. Workflow concurrency remains the
    serialization boundary for known OBI writers.
    """
    try:
        data = deepcopy(data)
        _ensure_meta(data)
        current = _get_remote()

        current_revision = _revision(current)
        if expected_revision is not None and current_revision != expected_revision:
            raise RuntimeError(
                "MEMORY_CONFLICT: expected revision "
                + str(expected_revision)
                + ", actual revision "
                + str(current_revision)
            )

        transactions = data[META_KEY].setdefault(TRANSACTIONS_KEY, [])
        if transaction_id and transaction_id in transactions:
            print("[MEMORY] Transaction already committed: " + transaction_id)
            return True

        data[META_KEY][REVISION_KEY] = current_revision + 1
        if transaction_id:
            transactions.append(transaction_id)
            del transactions[:-TRANSACTION_HISTORY_LIMIT]

        payload = {
            "files": {
                FILENAME: {
                    "content": json.dumps(data, indent=2)
                }
            }
        }
        r = requests.patch(
            "https://api.github.com/gists/" + str(GIST_ID),
            headers=HEADERS,
            json=payload,
            timeout=15,
        )
        if r.status_code != 200:
            raise RuntimeError(
                "Gist save failed - status " + str(r.status_code)
            )

        persisted = _get_remote()
        if _revision(persisted) != data[META_KEY][REVISION_KEY]:
            raise RuntimeError("MEMORY_VERIFY_FAILED: revision mismatch after save")

        print(
            "[MEMORY] Committed revision "
            + str(data[META_KEY][REVISION_KEY])
            + (
                " transaction " + transaction_id
                if transaction_id else ""
            )
        )
        return True
    except Exception as e:
        print("[MEMORY] Save failed: " + str(e))
        raise


def transaction(mutator, transaction_id: str = None) -> dict:
    """
    Execute one serialized OBI memory mutation.

    The mutation receives a private copy of the latest snapshot. The commit
    checks the snapshot revision and records the transaction ID. On a stale
    revision, the caller receives MEMORY_CONFLICT and can reload/reapply.

    Known GitHub Actions writers are serialized by the repository-wide
    obi-gist-writer concurrency group.
    """
    tx_id = transaction_id or uuid.uuid4().hex
    current = _get_remote()
    base_revision = _revision(current)

    if tx_id in current.get(META_KEY, {}).get(TRANSACTIONS_KEY, []):
        return current

    working = deepcopy(current)
    result = mutator(working)
    if result is not None:
        working = result

    save(
        working,
        expected_revision=base_revision,
        transaction_id=tx_id,
    )
    return working
