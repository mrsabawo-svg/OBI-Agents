"""
OBI Agents — Memory Core
Reads/writes agent memory to GitHub Gist.
"""
import os
import json
import requests

GIST_ID  = os.environ.get("GIST_ID")
GH_TOKEN = os.environ.get("GITHUB_TOKEN")
HEADERS  = {"Authorization": "token " + str(GH_TOKEN)}
FILENAME = "obi_memory.json"

def load() -> dict:
    try:
        r = requests.get(
            "https://api.github.com/gists/" + str(GIST_ID),
            headers=HEADERS,
            timeout=15
        )
        files = r.json().get("files", {})
        if FILENAME in files:
            content = files[FILENAME].get("content", "{}")
            return json.loads(content)
        return {}
    except Exception as e:
        print("[MEMORY] Load failed: " + str(e))
        return {}

def save(data: dict):
    try:
        payload = {"files": {FILENAME: {"content": json.dumps(data, indent=2)}}}
        requests.patch(
            "https://api.github.com/gists/" + str(GIST_ID),
            headers=HEADERS,
            json=payload,
            timeout=15
        )
        print("[MEMORY] Saved successfully")
    except Exception as e:
        print("[MEMORY] Save failed: " + str(e))
        print(f"[MEMORY] Using Gist: {GIST_ID}")
