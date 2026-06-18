"""
OBI TelegramCommandAgent
Polls for incoming Telegram commands and routes them to the correct agent/pipeline.
"""
import os
import json
import requests
from datetime import datetime
import pytz

SAST      = pytz.timezone("Africa/Johannesburg")
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
BASE_URL  = f"https://api.telegram.org/bot{BOT_TOKEN}"
OFFSET_FILE = "telegram_offset.json"   # persists between steps in the same run


# ── Telegram helpers ──────────────────────────────────────────────────────────

def _get_updates(offset: int) -> list:
    try:
        r = requests.get(
            f"{BASE_URL}/getUpdates",
            params={"offset": offset, "timeout": 5},
            timeout=10,
        )
        return r.json().get("result", [])
    except Exception as e:
        print(f"[CMD] getUpdates error: {e}")
        return []


def send(text: str, parse_mode: str = "Markdown") -> None:
    try:
        requests.post(
            f"{BASE_URL}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": parse_mode},
            timeout=10,
        )
    except Exception as e:
        print(f"[CMD] send error: {e}")


def _load_offset() -> int:
    try:
        with open(OFFSET_FILE) as f:
            return json.load(f).get("offset", 0)
    except Exception:
        return 0


def _save_offset(offset: int) -> None:
    try:
        with open(OFFSET_FILE, "w") as f:
            json.dump({"offset": offset}, f)
    except Exception as e:
        print(f"[CMD] offset save error: {e}")


# ── Command handlers ──────────────────────────────────────────────────────────

def handle_help() -> str:
    return (
        "*OBI Command Interface*\n\n"
        "`/signal <SYMBOL>` — run pipeline for one symbol\n"
        "  e.g. `/signal XAUUSD`\n\n"
        "`/market` — bias summary for all symbols\n\n"
        "`/health` — system health check\n\n"
        "`/status` — last pipeline run info\n\n"
        "`/approve <SYMBOL>` — execute pending trade\n"
        "  e.g. `/approve BTCUSD`\n\n"
        "`/skip <SYMBOL>` — dismiss pending trade\n"
        "  e.g. `/skip BTCUSD`\n\n"
        "`/help` — show this menu"
    )


def handle_signal(symbol: str) -> str:
    from agents.news_agent import NewsAgent
    from main import run

    symbol = symbol.upper().strip()
    VALID = {"XAUUSD", "EURUSD", "USDJPY", "GBPJPY", "GBPUSD",
             "BTCUSD", "ETHUSD", "SOLUSD", "NASDAQ"}

    if symbol not in VALID:
        return f"❌ Unknown symbol: `{symbol}`\nValid: {', '.join(sorted(VALID))}"

    send(f"🔍 Running pipeline for *{symbol}*…")

    news   = NewsAgent().is_safe()
    result = run(symbol, news)

    if result.get("fired"):
        return f"✅ *{symbol}* — signal fired. Check alert above ↑"
    if result.get("blocked"):
        reason = result["blocked"]
        labels = {
            "news":     "📰 News blackout active",
            "cooldown": "⏳ Cooldown — signal too recent",
            "session":  "🕐 Session not tradeable",
            "bias":     "📊 Bias not approved",
            "trigger":  "🎯 No trigger confirmed",
        }
        return f"⛔ *{symbol}* blocked — {labels.get(reason, reason)}"
    if result.get("data_empty"):
        return f"⚠️ *{symbol}* — no market data returned"
    if result.get("error"):
        return f"❌ *{symbol}* error: `{result['error']}`"

    return f"ℹ️ *{symbol}* — no result"


def handle_market() -> str:
    from agents.data_agent    import DataAgent
    from agents.htf_agent     import HTFAgent
    from agents.mtf_agent     import MTFAgent
    from agents.session_agent import SessionAgent
    from agents.bias_agent    import BiasAgent
    from agents.regime_agent  import RegimeAgent

    SYMBOLS = ["XAUUSD", "EURUSD", "USDJPY", "GBPJPY",
               "GBPUSD", "BTCUSD", "ETHUSD", "SOLUSD", "NASDAQ"]
    ALL_TF  = ["4h", "1h", "15m", "5m"]

    lines = [f"*OBI Market Summary* — {datetime.now(SAST).strftime('%H:%M SAST')}\n"]

    for sym in SYMBOLS:
        try:
            data    = DataAgent(sym).fetch(ALL_TF)
            if not data:
                lines.append(f"⚠️ `{sym}` — no data")
                continue
            session = SessionAgent(sym).analyse()
            htf     = HTFAgent(sym).analyse(data)
            regime  = RegimeAgent(sym).detect(data)
            mtf     = MTFAgent(sym).analyse(data, htf)
            bias    = BiasAgent(sym).evaluate(htf, mtf, session, regime)

            direction = bias.get("direction", "NEUTRAL")
            strength  = bias.get("strength", "")
            approved  = bias.get("approved", False)
            reg_label = regime.get("regime", "unknown")

            icon = "🟢" if direction == "LONG" else "🔴" if direction == "SHORT" else "⚪"
            tick = "✅" if approved else "⛔"

            lines.append(
                f"{icon} `{sym}` {direction} {strength} | {reg_label} | {tick}"
            )
        except Exception as e:
            lines.append(f"⚠️ `{sym}` — error: {e}")

    return "\n".join(lines)


def handle_health() -> str:
    from agents.health_agent import HealthAgent
    from core.memory         import load as load_memory

    mem  = load_memory() or {}
    now  = datetime.now(SAST)
    lines = [f"*OBI Health Check* — {now.strftime('%H:%M SAST')}\n"]

    for sym, data in mem.items():
        last = data.get("last_signal", "never")
        lines.append(f"• `{sym}` last signal: {last}")

    # Re-use HealthAgent text output by capturing stdout
    import io, sys
    buf = io.StringIO()
    sys.stdout = buf
    try:
        HealthAgent().check({})
    except Exception:
        pass
    sys.stdout = sys.__stdout__
    output = buf.getvalue().strip()
    if output:
        lines.append(f"\n```\n{output[:800]}\n```")

    return "\n".join(lines)


def handle_status() -> str:
    from core.memory import load as load_memory

    mem  = load_memory() or {}
    now  = datetime.now(SAST)
    lines = [f"*OBI Status* — {now.strftime('%d %b %Y %H:%M SAST')}\n"]

    fired  = [s for s, d in mem.items() if d.get("last_signal")]
    if fired:
        lines.append(f"Symbols with signal history: {len(fired)}/{len(mem)}\n")
        for sym in fired:
            lines.append(f"• `{sym}` — {mem[sym]['last_signal']}")
    else:
        lines.append("No signal history in memory yet.")

    return "\n".join(lines)


# ── Router ────────────────────────────────────────────────────────────────────

def route(text: str) -> str:
    text = text.strip()
    lower = text.lower()

    if lower == "/help":
        return handle_help()

    if lower == "/market":
        return handle_market()

    if lower == "/health":
        return handle_health()

    if lower == "/status":
        return handle_status()

    if lower.startswith("/signal"):
        parts = text.split()
        if len(parts) < 2:
            return "Usage: `/signal <SYMBOL>` — e.g. `/signal XAUUSD`"
        return handle_signal(parts[1])

    if lower.startswith("/approve"):
        parts = text.split()
        if len(parts) < 2:
            return "Usage: `/approve <SYMBOL>` — e.g. `/approve BTCUSD`"
        from agents.execution_agent import ExecutionAgent
        return ExecutionAgent(parts[1].upper()).approve(parts[1])

    if lower.startswith("/skip"):
        parts = text.split()
        if len(parts) < 2:
            return "Usage: `/skip <SYMBOL>` — e.g. `/skip BTCUSD`"
        from agents.execution_agent import ExecutionAgent
        return ExecutionAgent(parts[1].upper()).skip(parts[1])

    return f"❓ Unknown command: `{text}`\nType `/help` to see available commands."


# ── Main polling loop ─────────────────────────────────────────────────────────

def poll_and_process() -> None:
    offset  = _load_offset()
    updates = _get_updates(offset)

    if not updates:
        print("[CMD] No new messages.")
        return

    for update in updates:
        update_id = update["update_id"]
        offset    = update_id + 1

        msg  = update.get("message") or update.get("edited_message")
        if not msg:
            continue

        # Only respond to your own chat
        if str(msg.get("chat", {}).get("id")) != str(CHAT_ID):
            print(f"[CMD] Ignoring message from unknown chat {msg.get('chat', {}).get('id')}")
            continue

        text = msg.get("text", "").strip()
        if not text.startswith("/"):
            continue

        print(f"[CMD] Received: {text}")
        response = route(text)
        send(response)

    _save_offset(offset)
