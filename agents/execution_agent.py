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
        r = requests.get(
            f"{BYBIT_BASE}/v5/account/wallet-balance",
            params={"accountType": "UNIFIED", "coin": "USDT"},
            headers={
                "X-BAPI-API-KEY":   BYBIT_KEY,
                "X-BAPI-TIMESTAMP": ts,
                "X-BAPI-SIGN":      hmac.new(
                    BYBIT_SECRET.encode(),
                    f"{ts}{BYBIT_KEY}5000accountType=UNIFIED&coin=USDT".encode(),
                    hashlib.sha256
                ).hexdigest(),
                "X-BAPI-RECV-WINDOW": "5000",
            },
            timeout=10,
        )
        data = r.json()
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
