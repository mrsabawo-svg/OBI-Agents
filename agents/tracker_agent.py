"""
OBI Agents — Win Rate Tracker
Logs signal outcomes and builds accuracy memory per symbol.
Checks if previous signals hit TP1, TP2, TP3 or stopped out.
"""
import requests
import json
from core.memory import load as load_memory, save as save_memory

def check_outcome(symbol: str, ticker: str) -> dict:
    """
    Checks last signal outcome against current price.
    Updates memory with win/loss result.
    """
    try:
        import yfinance as yf
        df    = yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return {}

        current_price = float(df["Close"].squeeze().iloc[-1])
        memory        = load_memory()
        symbol_data   = memory.get(symbol, {})
        last_signal   = symbol_data.get("last_signal_data")

        if not last_signal:
            return {}

        direction = last_signal.get("direction")
        entry     = last_signal.get("entry", 0)
        sl        = last_signal.get("sl", 0)
        tp1       = last_signal.get("tp1", 0)
        tp2       = last_signal.get("tp2", 0)
        tp3       = last_signal.get("tp3", 0)

        if not all([entry, sl, tp1]):
            return {}

        outcome = None

        if direction == "BUY":
            if current_price >= tp3:
                outcome = "TP3"
            elif current_price >= tp2:
                outcome = "TP2"
            elif current_price >= tp1:
                outcome = "TP1"
            elif current_price <= sl:
                outcome = "SL"

        elif direction == "SELL":
            if current_price <= tp3:
                outcome = "TP3"
            elif current_price <= tp2:
                outcome = "TP2"
            elif current_price <= tp1:
                outcome = "TP1"
            elif current_price >= sl:
                outcome = "SL"

        if outcome:
            # Update memory
            wins   = symbol_data.get("wins", 0)
            losses = symbol_data.get("losses", 0)
            total  = symbol_data.get("total_signals", 0)

            if outcome in ["TP1", "TP2", "TP3"]:
                wins += 1
            else:
                losses += 1

            win_rate = round((wins / (wins + losses)) * 100, 1) if (wins + losses) > 0 else 0

            memory[symbol]["wins"]          = wins
            memory[symbol]["losses"]        = losses
            memory[symbol]["total_signals"] = total
            memory[symbol]["win_rate"]      = win_rate
            memory[symbol]["last_outcome"]  = outcome
            memory[symbol]["accuracy"]      = str(win_rate) + "% (" + str(wins) + "W/" + str(losses) + "L)"
            memory[symbol]["last_signal_data"] = None  # Clear after checking

            save_memory(memory)
            print("[TRACKER] " + symbol + ": " + outcome + " | Win rate: " + str(win_rate) + "%")
            return {"outcome": outcome, "win_rate": win_rate}

        return {"outcome": "PENDING", "win_rate": symbol_data.get("win_rate", 0)}

    except Exception as e:
        print("[TRACKER] " + symbol + " error: " + str(e))
        return {}
