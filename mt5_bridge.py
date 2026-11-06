"""
Weltrade MT5 Bridge Server
Exposes live Weltrade MT5 OHLCV data via a lightweight FastAPI/Flask HTTP endpoint
for DataAgent (MT5_BRIDGE_URL).

Usage:
1. Run MT5 terminal on Windows (or VPS with Wine/Windows).
2. pip install MetaTrader5 flask pytz
3. Set your account credentials below or via environment variables:
   MT5_LOGIN, MT5_PASSWORD, MT5_SERVER (e.g. "Weltrade-Demo" or "Weltrade-Live")
4. python mt5_bridge.py
5. Export MT5_BRIDGE_URL="http://localhost:5000" in your OBI environment.
"""

import os
from datetime import datetime
import pytz
from flask import Flask, request, jsonify

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

app = Flask(__name__)

MT5_LOGIN    = int(os.environ.get("MT5_LOGIN", "0"))
MT5_PASSWORD = os.environ.get("MT5_PASSWORD", "")
MT5_SERVER   = os.environ.get("MT5_SERVER", "Weltrade-Demo")
MT5_PATH     = os.environ.get("MT5_PATH", "")

TIMEFRAME_MAP = {
    "5m":  getattr(mt5, "TIMEFRAME_M5", 5) if mt5 else 5,
    "15m": getattr(mt5, "TIMEFRAME_M15", 15) if mt5 else 15,
    "1h":  getattr(mt5, "TIMEFRAME_H1", 60) if mt5 else 60,
    "4h":  getattr(mt5, "TIMEFRAME_H4", 240) if mt5 else 240,
}

SYMBOL_NAME_MAP = {
    "PAIN_999":   "PainX 999",
    "GAIN_999":   "GainX 999",
    "PAIN_1200":  "PainX 1200",
    "GAIN_1200":  "GainX 1200",
    "SFX_VOL_75": "SFX Vol 75",
}

def init_mt5():
    if mt5 is None:
        return False, "MetaTrader5 package not installed"

    init_kwargs = {}
    if MT5_PATH:
        init_kwargs["path"] = MT5_PATH

    if not mt5.initialize(**init_kwargs):
        return False, f"MT5 initialize failed: {mt5.last_error()}"

    if MT5_LOGIN and MT5_PASSWORD:
        authorized = mt5.login(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
        if not authorized:
            return False, f"MT5 login failed: {mt5.last_error()}"

    return True, "OK"


@app.route("/health", methods=["GET"])
def health():
    if mt5 is None:
        return jsonify({"status": "error", "message": "MetaTrader5 library not installed"}), 500
    terminal_info = mt5.terminal_info()
    if terminal_info is None:
        return jsonify({"status": "disconnected"}), 503
    return jsonify({"status": "connected", "connected": terminal_info.connected})


@app.route("/ohlcv", methods=["GET"])
def get_ohlcv():
    symbol_arg = request.args.get("symbol", "PAIN_999").upper()
    tf_arg     = request.args.get("timeframe", "15m").lower()
    count      = int(request.args.get("count", "300"))

    if mt5 is None:
        return jsonify({"error": "MetaTrader5 not installed"}), 500

    mt5_symbol = SYMBOL_NAME_MAP.get(symbol_arg, symbol_arg)
    mt5_tf     = TIMEFRAME_MAP.get(tf_arg, mt5.TIMEFRAME_M15)

    # Ensure symbol is selected in Market Watch
    if not mt5.symbol_select(mt5_symbol, True):
        return jsonify({"error": f"Symbol {mt5_symbol} not found in MT5"}), 404

    rates = mt5.copy_rates_from_pos(mt5_symbol, mt5_tf, 0, count)
    if rates is None or len(rates) == 0:
        return jsonify({"error": f"Failed to fetch rates: {mt5.last_error()}"}), 500

    data = []
    for r in rates:
        dt = datetime.fromtimestamp(r["time"], tz=pytz.UTC).strftime("%Y-%m-%d %H:%M:%S")
        data.append({
            "timestamp": dt,
            "Open":   float(r["open"]),
            "High":   float(r["high"]),
            "Low":    float(r["low"]),
            "Close":  float(r["close"]),
            "Volume": float(r["tick_volume"]),
        })

    return jsonify({"symbol": symbol_arg, "timeframe": tf_arg, "data": data})


if __name__ == "__main__":
    ok, msg = init_mt5()
    print(f"[BRIDGE] MT5 init status: {ok} ({msg})")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
