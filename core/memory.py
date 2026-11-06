"""
OBI Agents — Memory Core
Reads/writes agent memory to GitHub Gist.
"""
import os
import json
import requests

GIST_ID  = os.environ.get("GIST_ID")
GH_TOKEN = os.environ.get("GIST_TOKEN")
print("[MEMORY] Token prefix: " + str(GH_TOKEN)[:8] + "... (length=" + str(len(str(GH_TOKEN))) + ")")

HEADERS  = {"Authorization": "token " + str(GH_TOKEN)}
FILENAME = "obi_memory.json"

# In-memory snapshot to protect against overwriting state during network glitches
_last_known_memory = None

def load():
    global _last_known_memory
    try:
        r = requests.get(
            "https://api.github.com/gists/" + str(GIST_ID),
            headers=HEADERS,
            timeout=15
        )
        if r.status_code != 200:
            print("[MEMORY] Load failed - status " + str(r.status_code) + ": " + r.text[:200])
            return _last_known_memory if _last_known_memory is not None else None

        files = r.json().get("files", {})
        if FILENAME in files:
            content = files[FILENAME].get("content", "{}")
            parsed = json.loads(content)
            _last_known_memory = parsed
            return parsed
        return {}
    except Exception as e:
        print("[MEMORY] Load failed: " + str(e))
        return _last_known_memory if _last_known_memory is not None else None

def save(data: dict):
    global _last_known_memory
    if data is None:
        print("[MEMORY] Save aborted: data is None")
        return
    # Guard against accidental complete overwrite of existing memory with empty dict
    if not data and _last_known_memory:
        print("[MEMORY] Save aborted: refusing to overwrite non-empty memory with empty payload")
        return

    try:
        payload = {"files": {FILENAME: {"content": json.dumps(data, indent=2)}}}
        r = requests.patch(
            "https://api.github.com/gists/" + str(GIST_ID),
            headers=HEADERS,
            json=payload,
            timeout=15
        )
        if r.status_code == 200:
            _last_known_memory = data
            print("[MEMORY] Saved successfully")
        else:
            print("[MEMORY] Save failed - status " + str(r.status_code) + ": " + r.text[:200])
    except Exception as e:
        print("[MEMORY] Save failed: " + str(e))
