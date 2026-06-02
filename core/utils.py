"""
OBI Agents — Shared Utilities
"""
import pytz
from datetime import datetime

SAST = pytz.timezone("Africa/Johannesburg")

def now_sast():
    return datetime.now(SAST)

def is_kill_zone() -> dict:
    """Returns which kill zone is active right now (SAST)."""
    hour = now_sast().hour
    zones = {
        "London Open":   (9,  11),
        "New York Open": (15, 17),
        "Asian Session": (1,  3),
    }
    for name, (start, end) in zones.items():
        if start <= hour < end:
            return {"active": True, "name": name}
    return {"active": False, "name": None}

def sast_str():
    return now_sast().strftime("%Y-%m-%d %H:%M SAST")
