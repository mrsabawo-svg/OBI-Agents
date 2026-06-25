"""
OBI Agents — Execution Agent v1.0
Assisted execution on Bybit (crypto).
On confirmed signal: calculates trade plan, sends to Telegram for approval.
On /approve: fires the order (if not expired). On /skip: logs and moves on.
"""
import os
import json
import time
import hmac
import hashlib
import requests
from datetime import datetime
import pytz

SAST           = pytz.timezone("Africa/Johannesburg")
BOT_TOKEN      = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID        = os.environ.get("TELEGRAM_CHAT_ID", "")
BYBIT_KEY      = os.environ.get("BYBIT_API_KEY", "")
BYBIT_SECRET   = os.environ.get("BYBIT_API_SECRET", "")
# BYBIT_BASE = "https://api.bybit.com"          # live
BYBIT_BASE   = "https://api-testnet.bybit.com"  # testnet — swap to test

PLAN_EXPIRY_MINUTES = 10

CRYPTO_SYMBOLS = {"BTCUSD", "ETHUSD", "SOLUSD"}

BYBIT_TICKER = {
    "BTCUSD": "BTCUSDT",
    "ETHUSD": "ETHUSDT",
    "SOLUSD": "SOLUSDT",
}

RISK_PCT = {
    "LOW":    1.0,
    "MEDIUM": 0.75,
    "HIGH":   0.5,
}


# ── Bybit helpers ─────────────────────────────────────────────────────────────

def _sign(params: dict, secret: str) -> str:
    sorted_params = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(secret.encode(), sorted_params.encode(), hashlib.sha256).hexdigest()


def get_balance() -> float:
    try:
        ts = str(int(time.time() * 1000))
        params = "accountType=UNIFIED&coin=USDT"
        sign_str = f"{ts}{BYBIT_KEY}5000{params}"
        sig = hmac.new(
            BYBIT_SECRET.encode(),
            sign_str.encode(),
            hashlib.sha256
        ).hexdigest()
        r = requests.get(
            f"{BYBIT_BASE}/v5/account/wallet-balance",
            params={"accountType": "UNIFIED", "coin": "USDT"},
            headers={
                "X-BAPI-API-KEY":       BYBIT_KEY,
                "X-BAPI-TIMESTAMP":     ts,
                "X-BAPI-SIGN":          sig,
                "X-BAPI-RECV-WINDOW":   "5000",
            },
            timeout=10,
        )
        raw = r.text
        data = json.loads(raw)
        coins = (data.get("result", {})
                     .get("list", [{}])[0]
                     .get("coin", []))
        for c in coins:
            if c.get("coin") == "USDT":
                return float(c.get("walletBalance", 0))
        return 0.0
    except Exception as e:
        print(f"[EXEC] Balance error: {e}")
        return 0.0



def get_price(ticker: str) -> float:
    try:
        r = requests.get(
            f"{BYBIT_BASE}/v5/market/tickers",
            params={"category": "linear", "symbol": ticker},
            timeout=10,
        )
        data = r.json()
        return float(data["result"]["list"][0]["markPrice"])
    except Exception as e:
        print(f"[EXEC] Price error: {e}")
        return 0.0


def place_order(ticker: str, side: str, qty: float,
                sl: float, tp1: float) -> dict:
    try:
        ts = str(int(time.time() * 1000))
        body = {
            "category":       "linear",
            "symbol":         ticker,
            "side":           "Buy" if side == "BUY" else "Sell",
            "orderType":      "Market",
            "qty":            str(round(qty, 3)),
            "stopLoss":       str(round(sl, 2)),
            "takeProfit":     str(round(tp1, 2)),
            "timeInForce":    "GoodTillCancel",
            "positionIdx":    0,
        }
        sign_str = f"{ts}{BYBIT_KEY}5000{json.dumps(body)}"
        sig = hmac.new(BYBIT_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY":     BYBIT_KEY,
            "X-BAPI-TIMESTAMP":   ts,
            "X-BAPI-SIGN":        sig,
            "X-BAPI-RECV-WINDOW": "5000",
            "Content-Type":       "application/json",
        }
        r = requests.post(
            f"{BYBIT_BASE}/v5/order/create",
            headers=headers,
            json=body,
            timeout=10,
        )
        return r.json()
    except Exception as e:
        print(f"[EXEC] Order error: {e}")
        return {"error": str(e)}


# ── Telegram helpers ──────────────────────────────────────────────────────────

def _send(text: str) -> None:
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        print(f"[EXEC] Telegram error: {e}")
# ── Trade plan calculator ─────────────────────────────────────────────────────

def _calculate_qty(balance: float, risk_pct: float,
                   entry: float, sl: float) -> float:
    risk_amount = balance * (risk_pct / 100)
    sl_distance = abs(entry - sl)
    if sl_distance == 0:
        return 0.0
    qty = risk_amount / sl_distance
    return round(qty, 3)


def build_trade_plan(symbol: str, payload: dict) -> dict:
    trigger = payload.get("trigger", {})
    score   = payload.get("score", {})
    bias    = payload.get("bias", {})

    direction = bias.get("direction", "NEUTRAL")
    entry     = trigger.get("entry", 0.0)
    sl        = trigger.get("sl", 0.0)
    tp1       = trigger.get("tp1", 0.0)
    tp2       = trigger.get("tp2", 0.0)
    tp3       = trigger.get("tp3", 0.0)
    rr        = trigger.get("rr", 0.0)

    confidence = score.get("confidence", 50)
    grade      = score.get("grade", "C")
    risk_label = score.get("risk", "HIGH")
    risk_pct   = RISK_PCT.get(risk_label, 0.5)

    ticker  = BYBIT_TICKER.get(symbol, symbol)
    balance = get_balance()
    qty     = _calculate_qty(balance, risk_pct, entry, sl)

    return {
        "symbol":     symbol,
        "ticker":     ticker,
        "direction":  direction,
        "entry":      entry,
        "sl":         sl,
        "tp1":        tp1,
        "tp2":        tp2,
        "tp3":        tp3,
        "rr":         rr,
        "confidence": confidence,
        "grade":      grade,
        "risk":       risk_label,
        "risk_pct":   risk_pct,
        "balance":    balance,
        "qty":        qty,
        "timestamp":  datetime.now(SAST).strftime("%Y-%m-%d %H:%M SAST"),
    }


def format_plan_message(plan: dict) -> str:
    arrow = "🟢 LONG" if plan["direction"] == "BUY" else "🔴 SHORT"
    return (
        f"*OBI TRADE PLAN — {plan['symbol']}*\n"
        f"{'─' * 32}\n"
        f"{arrow} | Grade: {plan['grade']} | Risk: {plan['risk']}\n"
        f"Confidence: {plan['confidence']}%\n\n"
        f"Entry:  `{plan['entry']}`\n"
        f"SL:     `{plan['sl']}`\n"
        f"TP1:    `{plan['tp1']}`\n"
        f"TP2:    `{plan['tp2']}`\n"
        f"TP3:    `{plan['tp3']}`\n"
        f"RR:     `{plan['rr']}`\n\n"
        f"Balance: `${plan['balance']:.2f} USDT`\n"
        f"Risk:    `{plan['risk_pct']}% → ${plan['balance'] * plan['risk_pct'] / 100:.2f}`\n"
        f"Qty:     `{plan['qty']}`\n"
        f"{'─' * 32}\n"
        f"Reply `/approve {plan['symbol']}` to execute\n"
        f"Reply `/skip {plan['symbol']}` to dismiss\n"
        f"_Expires in {PLAN_EXPIRY_MINUTES} minutes_\n"
        f"{plan['timestamp']}"
    )


# ── Pending trade store (Gist-backed via memory) ──────────────────────────────

def save_pending(plan: dict) -> None:
    try:
        from core.memory import load as load_memory, save as save_memory
        mem = load_memory() or {}
        mem["_pending_trade"] = plan
        save_memory(mem)
    except Exception as e:
        print(f"[EXEC] Save pending error: {e}")


def load_pending() -> dict:
    try:
        from core.memory import load as load_memory
        mem = load_memory() or {}
        return mem.get("_pending_trade", {})
    except Exception as e:
        print(f"[EXEC] Load pending error: {e}")
        return {}


def clear_pending() -> None:
    try:
        from core.memory import load as load_memory, save as save_memory
        mem = load_memory() or {}
        mem.pop("_pending_trade", None)
        save_memory(mem)
    except Exception as e:
        print(f"[EXEC] Clear pending error: {e}")
# ── Main interface ────────────────────────────────────────────────────────────

class ExecutionAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def propose(self, payload: dict) -> None:
        if self.symbol not in CRYPTO_SYMBOLS:
            print(f"[EXEC] {self.symbol}: not a crypto symbol — skipping")
            return

        print(f"[EXEC] {self.symbol}: building trade plan")
        plan = build_trade_plan(self.symbol, payload)

        if plan["qty"] <= 0:
            print(f"[EXEC] {self.symbol}: qty=0 — insufficient balance or SL too tight")
            _send(f"⚠️ *{self.symbol}* — trade plan failed: qty=0. Check balance or SL distance.")
            return

        save_pending(plan)
        msg = format_plan_message(plan)
        _send(msg)
        print(f"[EXEC] {self.symbol}: trade plan sent to Telegram")

    def approve(self, symbol: str) -> str:
        """Called when user sends /approve SYMBOL."""
        plan = load_pending()
        if not plan:
            return "⚠️ No pending trade found. It may have expired."
        if plan.get("symbol") != symbol.upper():
            return f"⚠️ Pending trade is for *{plan.get('symbol')}*, not *{symbol}*."

        # Enforce expiry window
        try:
            plan_time = datetime.strptime(
                plan.get("timestamp", "").replace(" SAST", ""), "%Y-%m-%d %H:%M"
            )
            plan_time = SAST.localize(plan_time)
            age_minutes = (datetime.now(SAST) - plan_time).total_seconds() / 60
            if age_minutes > PLAN_EXPIRY_MINUTES:
                clear_pending()
                return f"⏰ *{plan['symbol']}* trade plan expired ({round(age_minutes)} min old). Wait for the next signal."
        except Exception as e:
            print(f"[EXEC] Expiry check error: {e}")

        _send(f"⚡ Executing *{plan['symbol']}* {plan['direction']} order…")

        result = place_order(
            ticker=plan["ticker"],
            side=plan["direction"],
            qty=plan["qty"],
            sl=plan["sl"],
            tp1=plan["tp1"],
        )

        clear_pending()

        if result.get("retCode") == 0:
            order_id = result.get("result", {}).get("orderId", "unknown")
            return (
                f"✅ *{plan['symbol']}* order placed\n"
                f"Order ID: `{order_id}`\n"
                f"Side: {plan['direction']} | Qty: {plan['qty']}\n"
                f"SL: `{plan['sl']}` | TP1: `{plan['tp1']}`"
            )
        else:
            err = result.get("retMsg", str(result))
            return f"❌ Order failed: `{err}`"

    def skip(self, symbol: str) -> str:
        plan = load_pending()
        if not plan or plan.get("symbol") != symbol.upper():
            return f"ℹ️ No pending trade for *{symbol}*."
        clear_pending()
        print(f"[EXEC] {symbol}: trade skipped by user")
        return f"⏭️ *{symbol}* trade skipped and cleared."
 
