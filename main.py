"""
OBI Intelligence v4.2 — Main Pipeline
"""
from agents.data_agent         import DataAgent
from agents.htf_agent          import HTFAgent
from agents.mtf_agent          import MTFAgent
from agents.ltf_agent          import LTFAgent
from agents.news_agent         import NewsAgent
from agents.digest_agent import DigestAgent

from agents.tracker_agent      import check_outcome
from agents.session_agent      import SessionAgent
from agents.bias_agent         import BiasAgent
from agents.zone_agent         import ZoneAgent
from agents.trigger_agent      import TriggerAgent
from agents.regime_agent       import RegimeAgent
from agents.health_agent       import HealthAgent
from agents.archive_agent      import ArchiveAgent
from agents.lifecycle_agent    import LifecycleAgent
from agents.edge_agent         import EdgeAgent
from agents.score_agent        import ScoreAgent
from agents.intelligence_agent import IntelligenceAgent
from core.utils                import sast_str
from core.memory               import load as load_memory
from datetime                  import datetime
import pytz

SAST    = pytz.timezone("Africa/Johannesburg")
SYMBOLS = ["XAUUSD", "EURUSD", "USDJPY", "GBPJPY", "GBPUSD", "BTCUSD", "ETHUSD", "SOLUSD", "NASDAQ"]
ALL_TF  = ["4h", "1h", "15m", "5m"]

def is_on_cooldown(symbol: str) -> bool:
    try:
        mem   = load_memory() or {}
        last  = mem.get(symbol, {}).get("last_signal", "")
        if not last:
            return False
        now   = datetime.now(SAST)
        lt    = datetime.strptime(last.replace(" SAST", ""), "%Y-%m-%d %H:%M")
        lt    = SAST.localize(lt)
        hours = (now - lt).total_seconds() / 3600
        if hours < 4:
            print("[MAIN] " + symbol + ": COOLDOWN - last signal " + str(round(hours, 1)) + "h ago")
            return True
        return False
    except Exception as e:
        print("[MAIN] Cooldown check error: " + str(e))
        return False

def run(symbol: str, news: dict = None) -> dict:
    print("=" * 45)
    print("  OBI v4.2 - " + symbol + " - " + sast_str())
    print("=" * 45)

    try:
        if news and not news["safe"]:
            print("[MAIN] " + symbol + ": NEWS BLOCK - " + news["reason"])
            return {"blocked": "news"}

        if is_on_cooldown(symbol):
            return {"blocked": "cooldown"}

        data = DataAgent(symbol).fetch(ALL_TF)
        if len(data) == 0:
            print("[MAIN] " + symbol + ": no data - skipping")
            return {"data_empty": True}

        ticker = DataAgent(symbol).ticker
        check_outcome(symbol, ticker)

        session = SessionAgent(symbol).analyse()
        if not session["tradeable"]:
            print("[MAIN] " + symbol + ": session blocked - " + session["reason"])
            return {"blocked": "session"}

        htf    = HTFAgent(symbol).analyse(data)
        regime = RegimeAgent(symbol).detect(data)
        mtf    = MTFAgent(symbol).analyse(data, htf)
        bias   = BiasAgent(symbol).evaluate(htf, mtf, session, regime)

        if not bias["approved"]:
            print("[MAIN] " + symbol + ": bias blocked - " + bias["reason"])
            return {"blocked": "bias"}

        zone    = ZoneAgent(symbol).analyse(data, bias)
        ltf     = LTFAgent(symbol).analyse(data, mtf, zone)
        trigger = TriggerAgent(symbol).evaluate(ltf, zone, bias)

        if not trigger["fire"]:
            print("[MAIN] " + symbol + ": no trigger - " + trigger["reason"])
            return {"blocked": "trigger"}

        edge  = EdgeAgent(symbol).analyse(trigger, bias, regime)
        score = ScoreAgent(symbol).compute(bias, trigger, regime, edge, session)

        payload = {
            "symbol":  symbol,
            "htf":     htf,
            "regime":  regime,
            "mtf":     mtf,
            "bias":    bias,
            "zone":    zone,
            "ltf":     ltf,
            "trigger": trigger,
            "session": session,
            "edge":    edge,
            "score":   score,
        }

        IntelligenceAgent(symbol).verdict(payload)
        ArchiveAgent().log(payload)

        return {"fired": True}

    except Exception as e:
        import traceback
        print("[MAIN] " + symbol + " error: " + str(e))
        print(traceback.format_exc())
        return {"error": str(e)}

if __name__ == "__main__":
    news = NewsAgent().is_safe()
    LifecycleAgent().check_open_signals()

    # Monday morning weekly digest
    DigestAgent().send_telegram_digest() if DigestAgent().should_run() else None

    results = {}
    for symbol in SYMBOLS:
        results[symbol] = run(symbol, news)

    HealthAgent().check(results)

