"""
OBI Agents - Lifecycle Agent
Tracks open signals through TP1/TP2/TP3/SL/expiry.

Lifecycle is the sole authority for mutating existing archive records.
TP1 and TP2 are milestones, not terminal outcomes. A trade remains OPEN
until TP3, SL, or expiry terminates it.
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


class LifecycleAgent:
    """Canonical state machine for existing archive records."""

    def check_open_signals(self):
        print("[LIFECYCLE] Checking open signals")
        try:
            memory = load_memory() or {}
            archive = memory.get("_archive", [])
            open_trades = [t for t in archive if t.get("status") == "OPEN"]

            closed = [
                t for t in archive
                if t.get("status") == "CLOSED"
                and t.get("outcome") not in ("EXPIRED", "AMBIGUOUS")
            ]
            print("[LIFECYCLE] Closed trades: " + str(len(closed)))

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

    def _close(self, trade: dict, outcome: str, now: datetime) -> bool:
        trade["status"] = "CLOSED"
        trade["outcome"] = outcome
        stamp = now.strftime("%Y-%m-%d %H:%M SAST")
        trade["closed"] = stamp
        trade["closed_at"] = stamp
        return True

    def _apply_candle(self, trade: dict, high: float, low: float, now: datetime) -> bool:
        # Closed records are terminal. Reprocessing an old candle must be a no-op.
        if trade.get("status") == "CLOSED":
            return False
        changed = False
        """
        Apply one OHLC candle to the lifecycle state machine.

        If a candle touches both the next actionable target and SL, the order
        of events is unknowable from OHLC data alone. The trade is left OPEN
        and the ambiguity is recorded; no outcome is fabricated.
        """
        direction = trade.get("direction")
        try:
            sl = float(trade.get("sl", 0))
            tp1 = float(trade.get("tp1", 0))
            tp2 = float(trade.get("tp2", 0))
            tp3 = float(trade.get("tp3", 0))
        except (TypeError, ValueError):
            return False

        if direction == "BUY":
            stop_hit = low <= sl
            if not trade.get("tp1_hit"):
                target_hit = high >= tp1
                if stop_hit and target_hit:
                    trade["resolution_note"] = "AMBIGUOUS_SAME_CANDLE_TARGET_AND_STOP"
                    return False
                if target_hit:
                    trade["tp1_hit"] = True
                    trade["outcome"] = "PENDING"
                    changed = True
                    print("[LIFECYCLE] " + str(trade.get("id", "")) + ": TP1 milestone")
                    # Continue through later targets if this same candle reached them.
            if trade.get("tp1_hit") and not trade.get("tp2_hit"):
                target_hit = high >= tp2
                if stop_hit and target_hit:
                    trade["resolution_note"] = "AMBIGUOUS_SAME_CANDLE_TARGET_AND_STOP"
                    return bool(trade.get("tp1_hit"))
                if target_hit:
                    trade["tp2_hit"] = True
                    trade["outcome"] = "PENDING"
                    changed = True
                    print("[LIFECYCLE] " + str(trade.get("id", "")) + ": TP2 milestone")
            if trade.get("tp2_hit") and not trade.get("tp3_hit"):
                target_hit = high >= tp3
                if stop_hit and target_hit:
                    trade["resolution_note"] = "AMBIGUOUS_SAME_CANDLE_TARGET_AND_STOP"
                    return bool(trade.get("tp1_hit") or trade.get("tp2_hit"))
                if target_hit:
                    trade["tp3_hit"] = True
                    self._close(trade, "TP3", now)
                    return True
            if stop_hit and not trade.get("status") == "CLOSED":
                self._close(trade, "SL", now)
                return True
            return changed

        if direction == "SELL":
            stop_hit = high >= sl
            if not trade.get("tp1_hit"):
                target_hit = low <= tp1
                if stop_hit and target_hit:
                    trade["resolution_note"] = "AMBIGUOUS_SAME_CANDLE_TARGET_AND_STOP"
                    return False
                if target_hit:
                    trade["tp1_hit"] = True
                    trade["outcome"] = "PENDING"
                    print("[LIFECYCLE] " + str(trade.get("id", "")) + ": TP1 milestone")
            if trade.get("tp1_hit") and not trade.get("tp2_hit"):
                target_hit = low <= tp2
                if stop_hit and target_hit:
                    trade["resolution_note"] = "AMBIGUOUS_SAME_CANDLE_TARGET_AND_STOP"
                    return bool(trade.get("tp1_hit"))
                if target_hit:
                    trade["tp2_hit"] = True
                    trade["outcome"] = "PENDING"
                    print("[LIFECYCLE] " + str(trade.get("id", "")) + ": TP2 milestone")
            if trade.get("tp2_hit") and not trade.get("tp3_hit"):
                target_hit = low <= tp3
                if stop_hit and target_hit:
                    trade["resolution_note"] = "AMBIGUOUS_SAME_CANDLE_TARGET_AND_STOP"
                    return bool(trade.get("tp1_hit") or trade.get("tp2_hit"))
                if target_hit:
                    trade["tp3_hit"] = True
                    self._close(trade, "TP3", now)
                    return True
            if stop_hit and not trade.get("status") == "CLOSED":
                self._close(trade, "SL", now)
                return True
            return changed

        return False

    def _check_trade(self, trade: dict, now: datetime) -> bool:
        symbol = trade.get("symbol")
        ticker = SYMBOL_MAP.get(symbol, symbol)

        opened_str = trade.get("opened", "")
        if opened_str:
            try:
                opened = datetime.strptime(
                    opened_str.replace(" SAST", ""), "%Y-%m-%d %H:%M"
                )
                opened = SAST.localize(opened)
                hours_open = (now - opened).total_seconds() / 3600
                if hours_open >= EXPIRY_HOURS:
                    self._close(trade, "EXPIRED", now)
                    print(
                        "[LIFECYCLE] " + str(symbol) + " "
                        + str(trade.get("id", "")) + ": EXPIRED after "
                        + str(round(hours_open, 1)) + "h"
                    )
                    return True
            except Exception:
                pass

        try:
            df = yf.download(
                ticker, period="1d", interval="5m",
                progress=False, auto_adjust=True, threads=False
            )
            if df is None or df.empty:
                return False
            high = float(df["High"].squeeze().iloc[-1])
            low = float(df["Low"].squeeze().iloc[-1])
        except Exception as e:
            print("[LIFECYCLE] Price fetch error for " + str(symbol) + ": " + str(e))
            return False

        changed = self._apply_candle(trade, high, low, now)

        if changed:
            print(
                "[LIFECYCLE] " + str(symbol) + " "
                + str(trade.get("id", "")) + ": "
                + str(trade.get("outcome")) + " | "
                + "TP1=" + str(bool(trade.get("tp1_hit"))) + " "
                + "TP2=" + str(bool(trade.get("tp2_hit"))) + " "
                + "TP3=" + str(bool(trade.get("tp3_hit")))
            )

        return changed

    def _print_streak(self, archive: list):
        closed = [
            t for t in archive
            if t.get("status") == "CLOSED"
            and t.get("outcome") not in ("EXPIRED", "AMBIGUOUS")
        ]
        if not closed:
            return

        closed_sorted = sorted(
            closed, key=lambda t: t.get("closed") or "", reverse=True
        )

        streak_type = None
        streak_count = 0
        for t in closed_sorted:
            result = (
                "WIN"
                if t.get("outcome") in ["TP1", "TP2", "TP3"]
                else "LOSS"
            )
            if streak_type is None:
                streak_type = result
                streak_count = 1
            elif result == streak_type:
                streak_count += 1
            else:
                break

        wins = len([
            t for t in closed
            if t.get("outcome") in ["TP1", "TP2", "TP3"]
        ])
        losses = len([t for t in closed if t.get("outcome") == "SL"])
        print(
            "[LIFECYCLE] Record: " + str(wins) + "W-"
            + str(losses) + "L | Current streak: "
            + str(streak_count) + " " + str(streak_type)
        )
