"""
OBI Agents - Lifecycle Agent
Tracks open signals and checks if they hit TP1/TP2/TP3/SL/expiry.
Updates the SAME archive that IntelligenceAgent writes to (core.memory),
so EdgeAgent sees real outcomes and win/loss streaks become visible.
"""
import yfinance as yf
from datetime import datetime
import pytz
from core.memory import load as load_memory, save as save_memory

SAST         = pytz.timezone("Africa/Johannesburg")
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


class LifecycleAgent:
    def check_open_signals(self):
        print("[LIFECYCLE] Checking open signals")
        try:
            memory  = load_memory() or {}
            archive = memory.get("_archive", [])
            open_trades = [t for t in archive if t.get("status") == "OPEN"]

            # Factor Analytics progress
            closed = [t for t in archive if t.get("status") == "CLOSED" and t.get("outcome") != "EXPIRED"]
            print("[LIFECYCLE] Closed trades: " + str(len(closed)) + "/30 needed for Factor Analytics")

            if not open_trades:
                print("[LIFECYCLE] No open signals to check")
                return

            print("[LIFECYCLE] Found " + str(len(open_trades)) + " open trade(s)")
            changed = False
            now = datetime.now(SAST)

            for trade in open_trades:
                if self._check_trade(trade, now):
                    changed = True

            if changed:
                save_memory(memory)
                self._print_streak(archive)

        except Exception as e:
            print("[LIFECYCLE] Error: " + str(e))

    def _check_trade(self, trade: dict, now: datetime) -> bool:
        symbol    = trade.get("symbol")
        ticker    = SYMBOL_MAP.get(symbol, symbol)
        direction = trade.get("direction")

        try:
            entry = float(trade.get("entry", 0))
            sl    = float(trade.get("sl", 0))
            tp1   = float(trade.get("tp1", 0))
            tp2   = float(trade.get("tp2", 0))
            tp3   = float(trade.get("tp3", 0))
        except (TypeError, ValueError):
            return False

        # Expiry check first
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
                    print("[LIFECYCLE] " + symbol + " " + trade.get("id", "") + ": EXPIRED after " + str(round(hours_open, 1)) + "h")
                    return True
            except Exception:
                pass

        try:
            df = yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=True, threads=False)
            if df is None or df.empty:
                return False

            # Extract high, low, close defensively to support intrabar candle evaluation
            def _extract_scalar(series_or_df):
                if hasattr(series_or_df, "squeeze"):
                    s = series_or_df.squeeze()
                    if hasattr(s, "iloc"):
                        v = s.iloc[-1]
                        return float(v.iloc[0]) if hasattr(v, "iloc") else float(v)
                    return float(s)
                elif hasattr(series_or_df, "iloc"):
                    v = series_or_df.iloc[-1]
                    return float(v.iloc[0]) if hasattr(v, "iloc") else float(v)
                return float(series_or_df)

            has_high = False
            has_low = False
            try:
                high_val = _extract_scalar(df["High"])
                has_high = True
            except Exception:
                high_val = None

            try:
                low_val = _extract_scalar(df["Low"])
                has_low = True
            except Exception:
                low_val = None

            try:
                close_val = _extract_scalar(df["Close"])
            except Exception:
                close_val = None

            if not has_high or high_val is None:
                high_val = close_val
            if not has_low or low_val is None:
                low_val = close_val

        except Exception as e:
            print("[LIFECYCLE] Price fetch error for " + str(symbol) + ": " + str(e))
            return False

        changed = False

        if direction == "BUY":
            # Intrabar detection: High checks upward targets, Low checks stop loss
            if high_val >= tp1 and not trade.get("tp1_hit"):
                trade["tp1_hit"] = True
                trade["status"]  = "CLOSED"
                trade["outcome"] = "TP1"
                trade["closed"]  = now.strftime("%Y-%m-%d %H:%M SAST")
                changed = True
            if not trade.get("status") == "CLOSED" and high_val >= tp2 and not trade.get("tp2_hit"):
                trade["tp2_hit"] = True
                trade["status"]  = "CLOSED"
                trade["outcome"] = "TP2"
                trade["closed"]  = now.strftime("%Y-%m-%d %H:%M SAST")
                changed = True
            if not trade.get("status") == "CLOSED" and high_val >= tp3:
                trade["tp3_hit"] = True
                trade["status"]  = "CLOSED"
                trade["outcome"] = "TP3"
                trade["closed"]  = now.strftime("%Y-%m-%d %H:%M SAST")
                changed = True
            elif not trade.get("status") == "CLOSED" and low_val <= sl:
                trade["status"]  = "CLOSED"
                trade["outcome"] = "SL"
                trade["closed"]  = now.strftime("%Y-%m-%d %H:%M SAST")
                changed = True

        elif direction == "SELL":
            # Intrabar detection: Low checks downward targets, High checks stop loss
            if low_val <= tp1 and not trade.get("tp1_hit"):
                trade["tp1_hit"] = True
                trade["status"]  = "CLOSED"
                trade["outcome"] = "TP1"
                trade["closed"]  = now.strftime("%Y-%m-%d %H:%M SAST")
                changed = True
            if not trade.get("status") == "CLOSED" and low_val <= tp2 and not trade.get("tp2_hit"):
                trade["tp2_hit"] = True
                trade["status"]  = "CLOSED"
                trade["outcome"] = "TP2"
                trade["closed"]  = now.strftime("%Y-%m-%d %H:%M SAST")
                changed = True
            if not trade.get("status") == "CLOSED" and low_val <= tp3:
                trade["tp3_hit"] = True
                trade["status"]  = "CLOSED"
                trade["outcome"] = "TP3"
                trade["closed"]  = now.strftime("%Y-%m-%d %H:%M SAST")
                changed = True
            elif not trade.get("status") == "CLOSED" and high_val >= sl:
                trade["status"]  = "CLOSED"
                trade["outcome"] = "SL"
                trade["closed"]  = now.strftime("%Y-%m-%d %H:%M SAST")
                changed = True

        if changed and trade.get("status") == "CLOSED":
            print("[LIFECYCLE] " + symbol + " " + trade.get("id", "") + ": " + trade.get("outcome") + " @ H:" + str(high_val) + "/L:" + str(low_val))

        return changed

    def _print_streak(self, archive: list):
        closed = [t for t in archive if t.get("status") == "CLOSED" and t.get("outcome") != "EXPIRED"]
        if not closed:
            return
        closed_sorted = sorted(closed, key=lambda t: t.get("closed") or "", reverse=True)

        streak_type  = None
        streak_count = 0
        for t in closed_sorted:
            result = "WIN" if t.get("outcome") in ["TP1", "TP2", "TP3"] else "LOSS"
            if streak_type is None:
                streak_type  = result
                streak_count = 1
            elif result == streak_type:
                streak_count += 1
            else:
                break

        wins   = len([t for t in closed if t.get("outcome") in ["TP1", "TP2", "TP3"]])
        losses = len([t for t in closed if t.get("outcome") == "SL"])
        print("[LIFECYCLE] Record: " + str(wins) + "W-" + str(losses) + "L | Current streak: " + str(streak_count) + " " + str(streak_type))
