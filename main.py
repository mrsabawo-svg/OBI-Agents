"""
OBI Intelligence v4.0 — Main Pipeline
"""
from agents.data_agent         import DataAgent
from agents.htf_agent          import HTFAgent
from agents.mtf_agent          import MTFAgent
from agents.ltf_agent          import LTFAgent
from agents.news_agent         import NewsAgent
from agents.tracker_agent      import check_outcome
from agents.session_agent      import SessionAgent
from agents.bias_agent         import BiasAgent
from agents.zone_agent         import ZoneAgent
from agents.trigger_agent      import TriggerAgent
from agents.intelligence_agent import IntelligenceAgent
from core.utils                import sast_str

SYMBOLS = ["XAUUSD", "EURUSD", "USDJPY", "GBPJPY", "GBPUSD", "BTCUSD", "ETHUSD", "SOLUSD", "NASDAQ"]
ALL_TF  = ["4h", "1h", "15m", "5m"]

def run(symbol: str, news: dict = None):
    print("=" * 45)
    print("  OBI v4.0 - " + symbol + " - " + sast_str())
    print("=" * 45)

    try:
        if news and not news["safe"]:
            print("[MAIN] " + symbol + ": NEWS BLOCK - " + news["reason"])
            return

        data = DataAgent(symbol).fetch(ALL_TF)
        if len(data) == 0:
            print("[MAIN] " + symbol + ": no data - skipping")
            return

        ticker = DataAgent(symbol).ticker
        check_outcome(symbol, ticker)

        session = SessionAgent(symbol).analyse()
        if not session["tradeable"]:
            print("[MAIN] " + symbol + ": session blocked - " + session["reason"])
            return

        htf = HTFAgent(symbol).analyse(data)
        mtf = MTFAgent(symbol).analyse(data, htf)

        bias = BiasAgent(symbol).evaluate(htf, mtf, session)
        if not bias["approved"]:
            print("[MAIN] " + symbol + ": bias blocked - " + bias["reason"])
            return

        zone    = ZoneAgent(symbol).analyse(data, bias)
        ltf     = LTFAgent(symbol).analyse(data, mtf, zone)
        trigger = TriggerAgent(symbol).evaluate(ltf, zone, bias)

        if not trigger["fire"]:
            print("[MAIN] " + symbol + ": no trigger - " + trigger["reason"])
            return

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
        print("[MAIN] " + symbol + " error: " + str(e))
        print(traceback.format_exc())

if __name__ == "__main__":
    news = NewsAgent().is_safe()
    for symbol in SYMBOLS:
        run(symbol, news)
