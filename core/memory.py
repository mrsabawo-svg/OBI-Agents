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
        if r.status_code != 200:
            print("[MEMORY] Load failed - status " + str(r.status_code) + ": " + r.text[:200])
            return {}
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
        r = requests.patch(
            "https://api.github.com/gists/" + str(GIST_ID),
            headers=HEADERS,
            json=payload,
            timeout=15
        )
        if r.status_code == 200:
            print("[MEMORY] Saved successfully")
        else:
            print("[MEMORY] Save failed - status " + str(r.status_code) + ": " + r.text[:200])
    except Exception as e:
        print("[MEMORY] Save failed: " + str(e))
