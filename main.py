"""
OBI Intelligence v4.0 — Main Pipeline
"""
from agents.data_agent         import DataAgent
from agents.htf_agent          import HTFAgent
from agents.mtf_agent          import MTFAgent
from agents.ltf_agent          import LTFAgent
from agents.news_agent import NewsAgent
from agents.tracker_agent import check_outcome
from agents.session_agent      import SessionAgent
from agents.bias_agent         import BiasAgent
from agents.zone_agent         import ZoneAgent
from agents.trigger_agent      import TriggerAgent
from agents.intelligence_agent import IntelligenceAgent
from core.utils                import sast_str

SYMBOLS = ["XAUUSD", "EURUSD", "USDJPY", "GBPJPY", "GBPUSD", "BTCUSD", "ETHUSD", "SOLUSD", "NASDAQ"]

ALL_TF  = ["4h", "1h", "15m", "5m"]

def run(symbol: str):
    print(f"\n{'═'*45}")
    print(f"  OBI v4.0 — {symbol} — {sast_str()}")
    print(f"{'═'*45}")

    try:
        # News gate
        news = NewsAgent().is_safe()
        if not news["safe"]:
            print("[MAIN] " + symbol + ": NEWS BLOCK — " + news["reason"])
            return

        # 1. Data
        data = DataAgent(symbol).fetch(ALL_TF)

        if len(data) == 0:
            print(f"[MAIN] {symbol}: no data — skipping")
            return
        # Check previous signal outcome
                ticker = DataAgent(symbol).ticker
                check_outcome(symbol, ticker)

        # 2. Session gate
        session = SessionAgent(symbol).analyse()
        if not session["tradeable"]:
            print(f"[MAIN] {symbol}: session blocked — {session['reason']}")
            return

        # 3. HTF macro bias
        htf = HTFAgent(symbol).analyse(data)

        # 4. MTF structure
        mtf = MTFAgent(symbol).analyse(data, htf)

        # 5. Bias gate
        bias = BiasAgent(symbol).evaluate(htf, mtf, session)
        if not bias["approved"]:
            print(f"[MAIN] {symbol}: bias blocked — {bias['reason']}")
            return

        # 6. Zone analysis
        zone = ZoneAgent(symbol).analyse(data, bias)

        # 7. LTF entry
        ltf = LTFAgent(symbol).analyse(data, mtf, zone)


        # 8. Trigger gate
        trigger = TriggerAgent(symbol).evaluate(ltf, zone, bias)
        if not trigger["fire"]:
            print(f"[MAIN] {symbol}: no trigger — {trigger['reason']}")
            return

        # 9. Intelligence verdict
        payload = {
            "symbol":  symbol,
            "htf":     htf,
            "mtf":     mtf,
            "bias":    bias,
            "zone":    zone,
            "ltf":     ltf,
            "trigger": trigger,
            "session": session,
        }
        IntelligenceAgent(symbol).verdict(payload)

    except Exception as e:
        import traceback
        print(f"[MAIN] {symbol} pipeline error: {e}")
        print(traceback.format_exc())

if __name__ == "__main__":
    for symbol in SYMBOLS:
        run(symbol)
