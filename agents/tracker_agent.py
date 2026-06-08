"""
OBI Agents — Tracker Agent
Manages trade lifecycle. Tracks TP1/TP2/TP3/SL hits.
Rebuilds stats from archive — never from manual counters.
"""
import yfinance as yf
from core.memory import load as load_memory, save as save_memory

SYMBOL_MAP = {
    "XAUUSD": "GLD",
    "EURUSD": "EURUSD=X",
    "USDJPY": "USDJPY=X",
    "GBPJPY": "GBPJPY=X",
    "GBPUSD": "GBPUSD=X",
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "SOLUSD": "SOL-USD",
    "NASDAQ": "QQQ",
}

def check_outcome(symbol: str, ticker: str):
    try:
        memory  = load_memory()
        archive = memory.get("_archive", [])

        open_trades = [
            t for t in archive
            if t.get("symbol") == symbol and t.get("status") == "OPEN"
        ]

        if not open_trades:
            return

        df = yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return

        current = float(df["Close"].squeeze().iloc[-1])
        changed = False

        for trade in open_trades:
            direction = trade.get("direction")
            entry     = trade.get("entry", 0)
            sl        = trade.get("sl", 0)
            tp1       = trade.get("tp1", 0)
            tp2       = trade.get("tp2", 0)
            tp3       = trade.get("tp3", 0)

            if direction == "BUY":
                if current >= tp1:
                    trade["tp1_hit"] = True
                if current >= tp2:
                    trade["tp2_hit"] = True
                if current >= tp3:
                    trade["tp3_hit"] = True
                    trade["status"]  = "CLOSED"
                    trade["outcome"] = "TP3"
                    trade["closed"]  = _now_sast()
                    changed = True
                elif current <= sl:
                    trade["status"]  = "CLOSED"
                    trade["outcome"] = "SL"
                    trade["closed"]  = _now_sast()
                    changed = True

            elif direction == "SELL":
                if current <= tp1:
                    trade["tp1_hit"] = True
                if current <= tp2:
                    trade["tp2_hit"] = True
                if current <= tp3:
                    trade["tp3_hit"] = True
                    trade["status"]  = "CLOSED"
                    trade["outcome"] = "TP3"
                    trade["closed"]  = _now_sast()
                    changed = True
                elif current >= sl:
                    trade["status"]  = "CLOSED"
                    trade["outcome"] = "SL"
                    trade["closed"]  = _now_sast()
                    changed = True

        if changed:
            # Rebuild stats from archive
            _rebuild_stats(memory, symbol)
            save_memory(memory)
            print("[TRACKER] " + symbol + ": stats rebuilt from archive")

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


def _now_sast() -> str:
    import pytz
    from datetime import datetime
    return datetime.now(pytz.timezone("Africa/Johannesburg")).strftime("%Y-%m-%d %H:%M SAST")
