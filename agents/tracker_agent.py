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

        df = yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=True, threads=False)
        if df is None or df.empty:
            return

        current = float(df["Close"].squeeze().iloc[-1])
        now     = datetime.now(SAST)
        changed = False

        for trade in open_trades:
            direction = trade.get("direction")
            entry     = float(trade.get("entry", 0))
            sl        = float(trade.get("sl", 0))
            tp1       = float(trade.get("tp1", 0))
            tp2       = float(trade.get("tp2", 0))
            tp3       = float(trade.get("tp3", 0))

            # Check expiry first
            opened_str = trade.get("opened", "")
            if opened_str:
                try:
                    opened = datetime.strptime(opened_str.replace(" SAST", ""), "%Y-%m-%d %H:%M")
                    opened = SAST.localize(opened)
                    hours_open = (now - opened).total_seconds() / 3600
                    if hours_open >= EXPIRY_HOURS:
                        trade["status"]  = "CLOSED"
                        trade["outcome"] = "EXPIRED"
                        trade["closed"]  = now.strftime("%Y-%m-%d %H:%M SAST")
                        changed = True
                        print("[TRACKER] " + symbol + ": EXPIRED after " + str(round(hours_open, 1)) + "h")
                        continue
                except:
                    pass

            if direction == "BUY":
                if current >= tp1 and not trade.get("tp1_hit"):
                    trade["tp1_hit"] = True
                    changed = True
                if current >= tp2 and not trade.get("tp2_hit"):
                    trade["tp2_hit"] = True
                    changed = True
                if current >= tp3:
                    trade["tp3_hit"] = True
                    trade["status"]  = "CLOSED"
                    trade["outcome"] = "TP3"
                    trade["closed"]  = now.strftime("%Y-%m-%d %H:%M SAST")
                    changed = True
                elif current <= sl:
                    trade["status"]  = "CLOSED"
                    trade["outcome"] = "SL"
                    trade["closed"]  = now.strftime("%Y-%m-%d %H:%M SAST")
                    changed = True

            elif direction == "SELL":
                if current <= tp1 and not trade.get("tp1_hit"):
                    trade["tp1_hit"] = True
                    changed = True
                if current <= tp2 and not trade.get("tp2_hit"):
                    trade["tp2_hit"] = True
                    changed = True
                if current <= tp3:
                    trade["tp3_hit"] = True
                    trade["status"]  = "CLOSED"
                    trade["outcome"] = "TP3"
                    trade["closed"]  = now.strftime("%Y-%m-%d %H:%M SAST")
                    changed = True
                elif current >= sl:
                    trade["status"]  = "CLOSED"
                    trade["outcome"] = "SL"
                    trade["closed"]  = now.strftime("%Y-%m-%d %H:%M SAST")
                    changed = True

        if changed:
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
