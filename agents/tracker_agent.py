"""
OBI Agents — Tracker Agent
Manages trade lifecycle. Tracks TP1/TP2/TP3/SL hits.
Includes trade expiry logic.
"""
import yfinance as yf
from datetime import datetime
import pytz
from core.memory import load as load_memory, save as save_memory

SAST = pytz.timezone("Africa/Johannesburg")
EXPIRY_HOURS = 48

SYMBOL_MAP = {
    "XAUUSD": "GC=F",
    "EURUSD": "EURUSD=X",
    "USDJPY": "USDJPY=X",
    "GBPJPY": "GBPJPY=X",
    "GBPUSD": "GBPUSD=X",
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "SOLUSD": "SOL-USD",
    "NASDAQ": "NQ=F",
}

from agents.lifecycle_agent import LifecycleAgent

def check_outcome(symbol: str, ticker: str):
    """
    Compatibility wrapper delegating exclusively to LifecycleAgent.
    Eliminates competing lifecycle implementations.
    """
    try:
        LifecycleAgent().check_open_signals()
    except Exception as e:
        print("[TRACKER] " + symbol + " error: " + str(e))


def _rebuild_stats(memory: dict, symbol: str):
    archive = memory.get("_archive", [])
    closed  = [t for t in archive if t.get("symbol") == symbol and t.get("status") == "CLOSED"]
    wins    = len([t for t in closed if t.get("outcome") in ["TP1", "TP2", "TP3"]])
    losses  = len([t for t in closed if t.get("outcome") == "SL"])
    total   = wins + losses
    wr      = round((wins / total) * 100, 1) if total > 0 else 0
    if symbol not in memory:
        memory[symbol] = {}
    memory[symbol]["wins"]     = wins
    memory[symbol]["losses"]   = losses
    memory[symbol]["win_rate"] = wr
    memory[symbol]["accuracy"] = str(wr) + "% (" + str(wins) + "W/" + str(losses) + "L)"
