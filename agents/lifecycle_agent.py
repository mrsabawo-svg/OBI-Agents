"""
OBI Agents — Lifecycle Agent
Tracks open signals and checks if they hit TP1/TP2/TP3 or SL.
Updates archive with real outcomes for EdgeAgent to learn from.
"""
import os
import requests
import yfinance as yf
from datetime import datetime
import pytz
from agents.archive_agent import ArchiveAgent

SAST = pytz.timezone("Africa/Johannesburg")

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


class LifecycleAgent:
    def __init__(self):
        self.archive = ArchiveAgent()

    def check_open_signals(self):
        print("[LIFECYCLE] Checking open signals")
        try:
            history = self.archive.get_history(limit=200)
            open_signals = [s for s in history if s.get("status") == "OPEN"]

            if not open_signals:
                print("[LIFECYCLE] No open signals to check")
                return

            for signal in open_signals:
                self._check_signal(signal)

        except Exception as e:
            print("[LIFECYCLE] Error: " + str(e))

    def _check_signal(self, signal: dict):
        symbol    = signal.get("symbol")
        ticker    = SYMBOL_MAP.get(symbol, symbol)
        direction = signal.get("direction")
        entry     = signal.get("entry", 0)
        sl        = signal.get("sl", 0)
        tp1       = signal.get("tp1", 0)
        tp2       = signal.get("tp2", 0)
        tp3       = signal.get("tp3", 0)
        signal_id = signal.get("id")

        try:
            df = yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=True)
            if df is None or df.empty:
                return

            current = float(df["Close"].squeeze().iloc[-1])
            outcome = None
            pnl     = 0

            if direction == "BUY":
                if current >= tp3:
                    outcome = "TP3"
                    pnl = round(abs(tp3 - entry), 5)
                elif current >= tp2:
                    outcome = "TP2"
                    pnl = round(abs(tp2 - entry), 5)
                elif current >= tp1:
                    outcome = "TP1"
                    pnl = round(abs(tp1 - entry), 5)
                elif current <= sl:
                    outcome = "SL"
                    pnl = -round(abs(entry - sl), 5)

            elif direction == "SELL":
                if current <= tp3:
                    outcome = "TP3"
                    pnl = round(abs(entry - tp3), 5)
                elif current <= tp2:
                    outcome = "TP2"
                    pnl = round(abs(entry - tp2), 5)
                elif current <= tp1:
                    outcome = "TP1"
                    pnl = round(abs(entry - tp1), 5)
                elif current >= sl:
                    outcome = "SL"
                    pnl = -round(abs(sl - entry), 5)

            if outcome:
                self.archive.update_outcome(signal_id, outcome, pnl)
                print("[LIFECYCLE] " + symbol + " " + signal_id + ": " + outcome + " pnl=" + str(pnl))

        except Exception as e:
            print("[LIFECYCLE] Check error for " + str(symbol) + ": " + str(e))
