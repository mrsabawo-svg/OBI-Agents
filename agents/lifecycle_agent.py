"""
OBI Agents — Lifecycle Agent
Tracks open signals through TP1/TP2/TP3/SL/expiry.

LifecycleAgent is the sole authority for mutating existing archive records.
TP1 and TP2 are milestones, not terminal outcomes. A trade remains OPEN
until TP3, SL, or expiry terminates it.
"""
import math
from datetime import datetime, timedelta

import pytz
import yfinance as yf

from agents.data_agent import DataAgent, SYNTHETIC_SYMBOLS
from core.memory import load as load_memory, save as save_memory
from core.archive_analytics_eligibility import LIFECYCLE_SCHEMA_VERSION

SAST = pytz.timezone("Africa/Johannesburg")
EXPIRY_HOURS = 48
REPLAY_DAYS = 7
CANDLE_MINUTES = 5
LIFECYCLE_EVENT_LIMIT = 100

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
    "PAIN_999": "PAIN_999",
    "GAIN_999": "GAIN_999",
    "PAIN_1200": "PAIN_1200",
    "GAIN_1200": "GAIN_1200",
    "SFX_VOL_75": "SFX_VOL_75",
}


class LifecycleAgent:
    """Canonical state machine for existing archive records."""

    def check_open_signals(self, symbol: str = None, now: datetime = None) -> bool:
        print("[LIFECYCLE] Checking open signals")
        try:
            memory = load_memory()
            if not isinstance(memory, dict):
                print("[LIFECYCLE] Memory snapshot is invalid; no lifecycle writes")
                return False

            archive = memory.get("_archive", [])
            open_trades = [
                trade for trade in archive
                if trade.get("status") == "OPEN"
                and (symbol is None or trade.get("symbol") == symbol)
            ]
            closed = [
                trade for trade in archive
                if trade.get("status") == "CLOSED"
                and trade.get("terminal_outcome") in ("TP3", "SL")
                and trade.get("lifecycle_schema_version") == LIFECYCLE_SCHEMA_VERSION
            ]
            print("[LIFECYCLE] Closed trades: " + str(len(closed)))

            if not open_trades:
                print("[LIFECYCLE] No open signals to check")
                return False

            now = now or datetime.now(SAST)
            if now.tzinfo is None:
                now = SAST.localize(now)

            print("[LIFECYCLE] Found " + str(len(open_trades)) + " open trade(s)")
            changed = False
            for trade in open_trades:
                if self._check_trade(trade, now):
                    changed = True

            if not changed:
                return False

            if save_memory(memory) is not True:
                print("[LIFECYCLE] Archive update was not persisted; durable state unchanged")
                return False

            self._print_streak(archive)
            return True
        except Exception as exc:
            print("[LIFECYCLE] Error: " + str(exc))
            return False

    @staticmethod
    def _parse_timestamp(value):
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            parsed = value
        else:
            text = str(value).strip().replace(" SAST", "")
            try:
                parsed = datetime.strptime(text, "%Y-%m-%d %H:%M")
            except ValueError:
                try:
                    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                except ValueError:
                    return None
        if parsed.tzinfo is None:
            parsed = SAST.localize(parsed)
        return parsed

    @staticmethod
    def _parse_candle_timestamp(value):
        if isinstance(value, datetime):
            parsed = value
        else:
            text = str(value).strip()
            if " SAST" in text:
                return LifecycleAgent._parse_timestamp(text)
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
        if parsed.tzinfo is None:
            parsed = pytz.utc.localize(parsed)
        return parsed

    @staticmethod
    def _row_number(row, field: str) -> float:
        value = row.get(field) if hasattr(row, "get") else row[field]
        if value is None and hasattr(row, "get"):
            value = row.get(field.lower())
        if hasattr(value, "iloc"):
            value = value.iloc[0]
        elif hasattr(value, "squeeze"):
            value = value.squeeze()
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite " + field)
        return number

    def _close(self, trade: dict, outcome: str, now: datetime) -> bool:
        trade["status"] = "CLOSED"
        trade["outcome"] = outcome
        trade["terminal_outcome"] = outcome
        trade["lifecycle_schema_version"] = LIFECYCLE_SCHEMA_VERSION
        trade["milestones"] = [
            name
            for name, hit in (
                ("TP1", trade.get("tp1_hit")),
                ("TP2", trade.get("tp2_hit")),
            )
            if hit
        ]
        stamp = now.astimezone(SAST).strftime("%Y-%m-%d %H:%M SAST")
        trade["closed"] = stamp
        trade["closed_at"] = stamp

        # Expiry is a lifecycle terminal state, not an assumed SL or TP fill.
        if outcome not in ("TP3", "SL"):
            trade["terminal_pnl_pips"] = None
            trade["pnl_basis"] = None
            return True

        try:
            entry = float(trade["entry"])
            direction = trade.get("direction")
            exit_level = float(trade["tp3"] if outcome == "TP3" else trade["sl"])
            symbol = str(trade.get("symbol", ""))
            pip_size = 0.01 if symbol.endswith("JPY") else 0.0001
            if symbol in {"BTCUSD", "ETHUSD"}:
                pip_size = 0.01
            elif symbol == "SOLUSD":
                pip_size = 0.0001
            elif symbol == "XAUUSD":
                pip_size = 0.01
            elif symbol == "NASDAQ":
                pip_size = 0.25
            delta = (exit_level - entry) if direction == "BUY" else (entry - exit_level)
            trade["terminal_pnl_pips"] = round(delta / pip_size, 2)
            trade["pnl_basis"] = "trigger_level"
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            trade["terminal_pnl_pips"] = None
            trade["pnl_basis"] = None
        return True

    def _apply_candle(self, trade: dict, high: float, low: float, now: datetime) -> bool:
        """Apply one OHLC candle without inventing intrabar event ordering."""
        if trade.get("status") == "CLOSED":
            return False
        try:
            high = float(high)
            low = float(low)
            entry = float(trade["entry"])
            sl = float(trade["sl"])
            tp1 = float(trade["tp1"])
            tp2 = float(trade["tp2"])
            tp3 = float(trade["tp3"])
        except (KeyError, TypeError, ValueError):
            return False
        if not all(math.isfinite(value) for value in (high, low, entry, sl, tp1, tp2, tp3)):
            return False
        if high < low:
            return False

        direction = trade.get("direction")
        if direction == "BUY":
            if not (sl < entry < tp1 <= tp2 <= tp3):
                return False
            stop_hit = low <= sl
            stages = (
                ("tp1_hit", tp1, high >= tp1, "TP1"),
                ("tp2_hit", tp2, high >= tp2, "TP2"),
                ("tp3_hit", tp3, high >= tp3, "TP3"),
            )
        elif direction == "SELL":
            if not (sl > entry > tp1 >= tp2 >= tp3):
                return False
            stop_hit = high >= sl
            stages = (
                ("tp1_hit", tp1, low <= tp1, "TP1"),
                ("tp2_hit", tp2, low <= tp2, "TP2"),
                ("tp3_hit", tp3, low <= tp3, "TP3"),
            )
        else:
            return False

        changed = False
        for flag, _target, target_hit, event in stages:
            if trade.get(flag):
                continue
            if stop_hit and target_hit:
                trade["resolution_note"] = "AMBIGUOUS_SAME_CANDLE_TARGET_AND_STOP"
                trade["lifecycle_event"] = "AMBIGUOUS"
                return True
            if not target_hit:
                break
            trade[flag] = True
            trade["outcome"] = "PENDING"
            trade.pop("resolution_note", None)
            trade["lifecycle_event"] = event
            changed = True
            if event == "TP3":
                self._close(trade, "TP3", now)
                return True

        if stop_hit and trade.get("status") != "CLOSED":
            trade.pop("resolution_note", None)
            trade["lifecycle_event"] = "SL"
            self._close(trade, "SL", now)
            return True
        return changed

    def _check_trade(self, trade: dict, now: datetime) -> bool:
        symbol = trade.get("symbol")
        opened = self._parse_timestamp(trade.get("opened") or trade.get("timestamp"))
        if opened is None and not trade.get("lifecycle_last_candle"):
            print("[LIFECYCLE] Missing opened timestamp for " + str(trade.get("id", "")))
            return False

        if opened is not None:
            hours_open = (
                now.astimezone(pytz.utc) - opened.astimezone(pytz.utc)
            ).total_seconds() / 3600
            if hours_open >= EXPIRY_HOURS:
                self._close(trade, "EXPIRED", now)
                trade["lifecycle_event"] = "EXPIRED"
                trade.setdefault("lifecycle_events", []).append({
                    "event": "EXPIRED",
                    "at": now.astimezone(pytz.utc).isoformat().replace("+00:00", "Z"),
                })
                print(
                    "[LIFECYCLE] " + str(symbol) + " "
                    + str(trade.get("id", "")) + ": EXPIRED after "
                    + str(round(hours_open, 1)) + "h"
                )
                return True

        try:
            if symbol in SYNTHETIC_SYMBOLS:
                frames = DataAgent(symbol).fetch(["5m"])
                df = frames.get("5m") if isinstance(frames, dict) else None
            else:
                ticker = SYMBOL_MAP.get(symbol, symbol)
                df = yf.download(
                    ticker,
                    period=str(REPLAY_DAYS) + "d",
                    interval="5m",
                    progress=False,
                    auto_adjust=True,
                    threads=False,
                )
            if df is None or getattr(df, "empty", False):
                return False

            last_cursor = self._parse_timestamp(trade.get("lifecycle_last_candle"))
            cursor = last_cursor or opened
            if cursor is None:
                return False
            cursor_utc = cursor.astimezone(pytz.utc)
            opened_utc = opened.astimezone(pytz.utc) if opened is not None else None
            now_utc = now.astimezone(pytz.utc)

            changed = False
            processed = 0
            for index, row in df.iterrows():
                candle_dt = self._parse_candle_timestamp(index)
                if candle_dt is None:
                    continue
                candle_utc = candle_dt.astimezone(pytz.utc)
                if candle_utc <= cursor_utc:
                    continue
                if opened_utc is not None and candle_utc <= opened_utc:
                    continue

                # MT5/Yahoo intraday timestamps identify candle opens. Do not
                # resolve outcomes using a candle that has not fully closed.
                candle_end_utc = candle_utc + timedelta(minutes=CANDLE_MINUTES)
                if candle_end_utc > now_utc:
                    continue

                bar_status = row.get("bar_status") if hasattr(row, "get") else None
                if bar_status is not None and str(bar_status).lower() not in {"closed", "complete"}:
                    continue
                validation_status = row.get("validation_status") if hasattr(row, "get") else None
                if validation_status is not None and str(validation_status).lower() not in {"valid", "complete"}:
                    continue
                quarantine_reasons = row.get("quarantine_reasons") if hasattr(row, "get") else None
                if quarantine_reasons:
                    continue
                closed_flag = row.get("closed") if hasattr(row, "get") else None
                if closed_flag is not None and not bool(closed_flag):
                    continue

                try:
                    high = self._row_number(row, "High")
                    low = self._row_number(row, "Low")
                except (KeyError, TypeError, ValueError, IndexError):
                    continue
                if high < low:
                    continue

                candle_changed = self._apply_candle(trade, high, low, now)
                trade["lifecycle_last_candle"] = (
                    candle_utc.isoformat().replace("+00:00", "Z")
                )
                event = trade.pop("lifecycle_event", None)
                if event:
                    events = trade.setdefault("lifecycle_events", [])
                    events.append({
                        "event": event,
                        "candle": trade["lifecycle_last_candle"],
                        "note": trade.get("resolution_note"),
                    })
                    if len(events) > LIFECYCLE_EVENT_LIMIT:
                        del events[:-LIFECYCLE_EVENT_LIMIT]

                processed += 1
                changed = True
                cursor_utc = candle_utc
                if trade.get("status") == "CLOSED":
                    break

            if processed:
                print(
                    "[LIFECYCLE] " + str(symbol) + " "
                    + str(trade.get("id", "")) + ": "
                    + str(trade.get("outcome")) + " | "
                    + "TP1=" + str(bool(trade.get("tp1_hit"))) + " "
                    + "TP2=" + str(bool(trade.get("tp2_hit"))) + " "
                    + "TP3=" + str(bool(trade.get("tp3_hit"))) + " | "
                    + "candles=" + str(processed)
                )
            return changed
        except Exception as exc:
            print("[LIFECYCLE] Price fetch error for " + str(symbol) + ": " + str(exc))
            return False

    def _print_streak(self, archive: list):
        closed = [
            trade for trade in archive
            if trade.get("status") == "CLOSED"
            and trade.get("terminal_outcome") in ("TP3", "SL")
            and trade.get("lifecycle_schema_version") == LIFECYCLE_SCHEMA_VERSION
        ]
        if not closed:
            return
        closed_sorted = sorted(
            closed, key=lambda trade: trade.get("closed") or "", reverse=True
        )
        streak_type = None
        streak_count = 0
        for trade in closed_sorted:
            result = (
                "WIN"
                if trade.get("terminal_outcome") == "TP3"
                else "LOSS"
            )
            if streak_type is None:
                streak_type = result
                streak_count = 1
            elif result == streak_type:
                streak_count += 1
            else:
                break
        wins = sum(1 for trade in closed if trade.get("terminal_outcome") == "TP3")
        losses = sum(1 for trade in closed if trade.get("terminal_outcome") == "SL")
        print(
            "[LIFECYCLE] Record: " + str(wins) + "W-"
            + str(losses) + "L | Current streak: "
            + str(streak_count) + " " + str(streak_type)
        )
