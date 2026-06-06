"""
OBI Agents — News Filter Agent
Blocks signals during high impact economic events.
Protects against news-driven whipsaws.
"""
import requests
from datetime import datetime, timedelta
import pytz

SAST = pytz.timezone("Africa/Johannesburg")

# High impact events to block
BLOCKED_KEYWORDS = [
    "NFP", "Non-Farm", "Federal Reserve", "Fed Rate", "FOMC",
    "ECB", "Bank of England", "BOE", "BOJ", "CPI", "GDP",
    "Interest Rate", "Unemployment", "Retail Sales", "PMI"
]

# Minutes to block before and after event
BLOCK_BEFORE = 30
BLOCK_AFTER  = 30


class NewsAgent:
    def __init__(self):
        self.events = []

    def is_safe(self) -> dict:
        """
        Returns: { safe: bool, reason: str, event: str }
        """
        try:
            self.events = self._fetch_events()
            now = datetime.now(SAST)

            for event in self.events:
                event_time = event.get("time")
                event_name = event.get("name", "")
                impact     = event.get("impact", "")

                if not event_time:
                    continue

                # Only block high impact
                if impact != "High":
                    continue

                diff = (event_time - now).total_seconds() / 60

                # Block window: 30 mins before to 30 mins after
                if -BLOCK_AFTER <= diff <= BLOCK_BEFORE:
                    return {
                        "safe":   False,
                        "reason": "High impact news: " + event_name,
                        "event":  event_name,
                        "mins":   round(diff)
                    }

            return {"safe": True, "reason": "No high impact events", "event": None, "mins": None}

        except Exception as e:
            print("[NEWS] Error: " + str(e) + " — defaulting to safe")
            return {"safe": True, "reason": "News check failed — proceeding", "event": None, "mins": None}

    def _fetch_events(self) -> list:
        try:
            today = datetime.now(SAST).strftime("%Y-%m-%d")
            r = requests.get(
                "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
                timeout=10
            )
            raw    = r.json()
            events = []

            for item in raw:
                if item.get("impact") != "High":
                    continue

                try:
                    dt_str = item.get("date", "")
                    if not dt_str:
                        continue

                    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                    dt = dt.astimezone(SAST)
                    name = item.get("title", "Unknown")

                    events.append({
                        "name":   name,
                        "time":   dt,
                        "impact": "High"
                    })
                except:
                    continue

            print("[NEWS] Fetched " + str(len(events)) + " high impact events this week")
            return events

        except Exception as e:
            print("[NEWS] Fetch error: " + str(e))
            return []
