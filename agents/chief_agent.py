"""
OBI Agents — Chief Agent v1.0
Orchestrates the full pipeline with intelligent symbol prioritisation.
Decides what to run, in what order, and why.
"""
import pytz
from datetime import datetime
from core.memory import load as load_memory

SAST = pytz.timezone("Africa/Johannesburg")

SYMBOLS = ["XAUUSD", "EURUSD", "USDJPY", "GBPJPY", "GBPUSD",
           "BTCUSD", "ETHUSD", "SOLUSD", "NASDAQ"]

CRYPTO   = {"BTCUSD", "ETHUSD", "SOLUSD"}
FOREX    = {"XAUUSD", "EURUSD", "USDJPY", "GBPJPY", "GBPUSD"}
INDICES  = {"NASDAQ"}

# Session windows in SAST hours
SESSION_WINDOWS = {
    "Asian Session":  (1,  9),
    "London Open":    (9,  12),
    "New York Open":  (14, 18),
    "London Close":   (18, 20),
}

# Which symbols are most relevant per session
SESSION_PRIORITY = {
    "Asian Session":  ["USDJPY", "BTCUSD", "ETHUSD", "SOLUSD"],
    "London Open":    ["XAUUSD", "EURUSD", "GBPJPY", "GBPUSD", "BTCUSD"],
    "New York Open":  ["XAUUSD", "EURUSD", "NASDAQ", "BTCUSD", "ETHUSD"],
    "London Close":   ["XAUUSD", "EURUSD", "GBPJPY"],
}


# ── Session detection ─────────────────────────────────────────────────────────

def _active_session(hour: int) -> str:
    for name, (start, end) in SESSION_WINDOWS.items():
        if start <= hour < end:
            return name
    return "Off Hours"


def _get_recent_scores(mem: dict) -> dict:
    """Pull last known confidence scores from memory."""
    scores = {}
    for sym in SYMBOLS:
        scores[sym] = mem.get(sym, {}).get("last_confidence", 50)
    return scores


# ── Priority engine ───────────────────────────────────────────────────────────

def _prioritise(hour: int, weekday: int, mem: dict) -> list:
    """
    Returns SYMBOLS sorted by priority score.
    Factors:
      1. Session relevance (40pts)
      2. Recent confidence score from memory (30pts)
      3. Crypto always-on bonus (20pts)
      4. Recency penalty — symbols scanned recently score lower (10pts)
    """
    session      = _active_session(hour)
    session_syms = SESSION_PRIORITY.get(session, [])
    recent_scores = _get_recent_scores(mem)
    now          = datetime.now(SAST)

    priority = {}
    for sym in SYMBOLS:
        score = 0

        # 1. Session relevance
        if sym in session_syms:
            idx = session_syms.index(sym)
            score += max(40 - idx * 8, 10)   # top pick = 40, each rank -8

        # 2. Recent confidence from memory
        last_conf = recent_scores.get(sym, 50)
        score += int(last_conf * 0.30)         # max 30pts at conf=100

        # 3. Crypto 24/7 bonus during off hours
        if sym in CRYPTO and session == "Off Hours":
            score += 20

        # 4. Recency penalty — avoid re-scanning too soon
        last_signal = mem.get(sym, {}).get("last_signal", "")
        if last_signal:
            try:
                lt    = datetime.strptime(last_signal.replace(" SAST", ""), "%Y-%m-%d %H:%M")
                lt    = SAST.localize(lt)
                hours = (now - lt).total_seconds() / 3600
                if hours < 2:
                    score -= 10
            except Exception:
                pass

        # Weekend: skip non-crypto entirely
        if weekday >= 5 and sym not in CRYPTO:
            score = -999

        priority[sym] = score

    ranked = sorted(SYMBOLS, key=lambda s: priority[s], reverse=True)
    return ranked, session, priority


# ── Task types ────────────────────────────────────────────────────────────────

class Task:
    FULL_SCAN    = "full_scan"
    SINGLE       = "single_signal"
    MARKET_BRIEF = "market_brief"
    HEALTH       = "health"
    STATUS       = "status"


# ── Chief Agent ───────────────────────────────────────────────────────────────

class ChiefAgent:
    def __init__(self):
        self.now     = datetime.now(SAST)
        self.hour    = self.now.hour
        self.weekday = self.now.weekday()
        self.mem     = load_memory() or {}

    def decide(self, task_type: str, symbol: str = None) -> dict:
        """
        Entry point. Returns a decision dict:
        {
            "task":     task type,
            "symbols":  ordered list to scan,
            "session":  active session name,
            "reason":   why this order was chosen,
            "priority": full priority scores
        }
        """
        if task_type == Task.SINGLE:
            return {
                "task":     Task.SINGLE,
                "symbols":  [symbol.upper()],
                "session":  _active_session(self.hour),
                "reason":   f"Manual request for {symbol}",
                "priority": {symbol: 100},
            }

        if task_type == Task.MARKET_BRIEF:
            ranked, session, priority = _prioritise(self.hour, self.weekday, self.mem)
            return {
                "task":     Task.MARKET_BRIEF,
                "symbols":  ranked,
                "session":  session,
                "reason":   f"Market brief — {session}",
                "priority": priority,
            }

        if task_type == Task.FULL_SCAN:
            ranked, session, priority = _prioritise(self.hour, self.weekday, self.mem)

            # Chief recommendation — top 3 symbols to focus on
            top3 = [s for s in ranked if priority.get(s, 0) > 0][:3]

            reason = self._build_reason(session, top3, priority)

            return {
                "task":     Task.FULL_SCAN,
                "symbols":  ranked,
                "session":  session,
                "top":      top3,
                "reason":   reason,
                "priority": priority,
            }

        # Health / status — no symbol ordering needed
        return {
            "task":    task_type,
            "symbols": [],
            "session": _active_session(self.hour),
            "reason":  task_type,
            "priority": {},
        }

    def _build_reason(self, session: str, top3: list, priority: dict) -> str:
        if not top3:
            return f"No high-priority symbols for {session}"
        scores = ", ".join(f"{s}({priority.get(s,0)})" for s in top3)
        return f"{session} — top picks: {scores}"

    def brief(self) -> str:
        """
        Returns a human-readable Chief briefing for Telegram.
        Used at the start of each full scan run.
        """
        decision = self.decide(Task.FULL_SCAN)
        session  = decision["session"]
        top3     = decision.get("top", [])
        priority = decision["priority"]
        now_str  = self.now.strftime("%H:%M SAST")

        lines = [
            f"*OBI Chief Briefing — {now_str}*",
            f"Session: {session}\n",
        ]

        if top3:
            lines.append("*Priority symbols:*")
            for sym in top3:
                score = priority.get(sym, 0)
                tag   = "🟡 Crypto" if sym in CRYPTO else ("📈 Index" if sym in INDICES else "💱 Forex")
                lines.append(f"  {tag} `{sym}` — score {score}")
        else:
            lines.append("No high-priority symbols right now.")

        skipped = [s for s in SYMBOLS if priority.get(s, -999) < 0]
        if skipped:
            lines.append(f"\n_Skipped (weekend/off): {', '.join(skipped)}_")

        return "\n".join(lines)
