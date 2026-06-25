"""
OBI TelegramCommandAgent
Polls for incoming Telegram commands and routes them through ChiefAgent.
"""
import os
import requests
from datetime import datetime
import pytz

SAST      = pytz.timezone("Africa/Johannesburg")
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
BASE_URL  = f"https://api.telegram.org/bot{BOT_TOKEN}"


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


def _load_offset() -> tuple[int, int]:
    try:
        from core.memory import load as load_memory
        mem = load_memory() or {}
        return mem.get("_telegram_offset", 0), mem.get("_last_processed_update", 0)
    except Exception as e:
        print(f"[CMD] offset load error: {e}")
        return 0, 0



def _save_offset(offset: int, last_processed: int) -> bool:
    try:
        from core.memory import load as load_memory, save as save_memory
        mem = load_memory() or {}
        mem["_telegram_offset"] = offset
        mem["_last_processed_update"] = last_processed
        save_memory(mem)
        return True
    except Exception as e:
        print(f"[CMD] offset save error: {e}")
        return False



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
    from agents.news_agent  import NewsAgent
    from agents.chief_agent import ChiefAgent, Task
    from main import run

    symbol = symbol.upper().strip()
    VALID  = {"XAUUSD", "EURUSD", "USDJPY", "GBPJPY", "GBPUSD",
              "BTCUSD", "ETHUSD", "SOLUSD", "NASDAQ"}

    if symbol not in VALID:
        return f"❌ Unknown symbol: `{symbol}`\nValid: {', '.join(sorted(VALID))}"

    send(f"🔍 Running pipeline for *{symbol}*…")

    chief    = ChiefAgent()
    decision = chief.decide(Task.SINGLE, symbol=symbol)
    print(f"[CMD] Chief decision: {decision['reason']}")

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
    from agents.chief_agent   import ChiefAgent, Task

    ALL_TF = ["4h", "1h", "15m", "5m"]

    chief    = ChiefAgent()
    decision = chief.decide(Task.MARKET_BRIEF)
    symbols  = decision["symbols"]
    priority = decision["priority"]
    session  = decision["session"]

    lines = [
        f"*OBI Market Summary — {datetime.now(SAST).strftime('%H:%M SAST')}*",
        f"_Session: {session}_\n"
    ]

    for sym in symbols:
        if priority.get(sym, 0) < 0:
            continue
        try:
            data = DataAgent(sym).fetch(ALL_TF)
            if not data:
                lines.append(f"⚠️ `{sym}` — no data")
                continue
            session_data = SessionAgent(sym).analyse()
            htf    = HTFAgent(sym).analyse(data)
            regime = RegimeAgent(sym).detect(data)
            mtf    = MTFAgent(sym).analyse(data, htf)
            bias   = BiasAgent(sym).evaluate(htf, mtf, session_data, regime)

            direction = bias.get("direction", "NEUTRAL")
            approved  = bias.get("approved", False)
            reg_label = regime.get("label", "unknown")
            pri_score = priority.get(sym, 0)

            icon = "🟢" if direction == "BUY" else "🔴" if direction == "SELL" else "⚪"
            tick = "✅" if approved else "⛔"

            lines.append(f"{icon} `{sym}` {direction} | {reg_label} | {tick} | p={pri_score}")
        except Exception as e:
            lines.append(f"⚠️ `{sym}` — error: {e}")

    return "\n".join(lines)


def handle_health() -> str:
    from agents.health_agent import HealthAgent
    from core.memory         import load as load_memory

    mem   = load_memory() or {}
    now   = datetime.now(SAST)
    lines = [f"*OBI Health Check* — {now.strftime('%H:%M SAST')}\n"]

    for sym, data in mem.items():
        if sym.startswith("_"):
            continue
        last = data.get("last_signal", "never")
        lines.append(f"• `{sym}` last signal: {last}")

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
    from core.memory        import load as load_memory
    from agents.chief_agent import ChiefAgent

    mem   = load_memory() or {}
    now   = datetime.now(SAST)
    chief = ChiefAgent()

    lines = [f"*OBI Status* — {now.strftime('%d %b %Y %H:%M SAST')}\n"]
    lines.append(chief.brief())
    lines.append("")

    fired = [s for s, d in mem.items() if not s.startswith("_") and d.get("last_signal")]
    if fired:
        lines.append(f"Signal history ({len(fired)} symbols):")
        for sym in fired:
            lines.append(f"• `{sym}` — {mem[sym]['last_signal']}")
    else:
        lines.append("No signal history in memory yet.")

    return "\n".join(lines)


# ── Router ────────────────────────────────────────────────────────────────────

def route(text: str) -> str:
    text  = text.strip()
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
    offset, last_processed = _load_offset()
    updates = _get_updates(offset)

    if not updates:
        print("[CMD] No new messages.")
        return

    print(f"[CMD] Fetched {len(updates)} update(s) starting from offset {offset}")

    for update in updates:
        update_id = update["update_id"]

        if update_id <= last_processed:
            print(f"[CMD] Skipping already processed update_id={update_id}")
            continue

        if not _save_offset(update_id + 1, update_id):
            print(f"[CMD] CRITICAL: offset save failed for update_id={update_id} — aborting")
            return

        offset = update_id + 1
        last_processed = update_id

        msg = update.get("message") or update.get("edited_message")
        if not msg:
            continue

        if str(msg.get("chat", {}).get("id")) != str(CHAT_ID):
            print(f"[CMD] Ignoring message from unknown chat")
            continue

        text = msg.get("text", "").strip()
        if not text.startswith("/"):
            continue

        print(f"[CMD] Received: {text}")
        try:
            response = route(text)
            send(response)
        except Exception as e:
            print(f"[CMD] Error handling '{text}': {e}")

