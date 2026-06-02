"""
OBI Agents — Memory Core
Reads/writes agent memory to GitHub Gist.
"""
import os, json, requests

GIST_ID    = os.environ.get("MEMORY_GIST_ID")
GH_TOKEN   = os.environ.get("GITHUB_TOKEN")
HEADERS    = {"Authorization": f"token {GH_TOKEN}"}
FILENAME   = "obi_memory.json"

def load() -> dict:
    try:
        r = requests.get(f"https://api.github.com/gists/{GIST_ID}", headers=HEADERS)
        content = r.json()["files"][FILENAME]["content"]
        return json.loads(content)
    except:
        return {}

def save(data: dict):
    try:
        payload = {"files": {FILENAME: {"content": json.dumps(data, indent=2)}}}
        requests.patch(f"https://api.github.com/gists/{GIST_ID}", headers=HEADERS, json=payload)
    except Exception as e:
        print(f"[MEMORY] Save failed: {e}")
