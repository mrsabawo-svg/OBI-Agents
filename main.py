"""
OBI Intelligence v4.2 — Main Pipeline
Copyright © Mazvita Sabawo. All rights reserved.
"""
import uuid

from agents.data_agent         import DataAgent
from agents.htf_agent          import HTFAgent
from agents.mtf_agent          import MTFAgent
from agents.ltf_agent          import LTFAgent
from agents.news_agent         import NewsAgent
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
from agents.execution_agent    import ExecutionAgent
from agents.persistence_agent  import PersistenceAgent
from agents.notifier_agent     import NotifierAgent
from agents.chief_agent        import ChiefAgent
from core.models               import SymbolContext
from core.memory               import load as load_memory
from core.utils                import sast_str

SYMBOLS = ["XAUUSD", "EURUSD", "USDJPY", "GBPJPY", "GBPUSD", "BTCUSD", "ETHUSD", "SOLUSD", "NASDAQ"]
ALL_TF  = ["4h", "1h", "15m", "5m"]


def build_signal_id(symbol: str) -> str:
    """
    Build a human-readable canonical signal identity.

    The timestamp identifies the generation window; the nonce prevents
    same-symbol signals generated within the same minute from colliding.
    """
    stamp = sast_str().replace("-", "").replace(":", "").replace(" ", "_")
    return symbol + "_" + stamp + "_" + uuid.uuid4().hex[:8]


def run(symbol: str, news: dict = None) -> dict:
    print("=" * 45)
    print("  OBI v4.2 - " + symbol + " - " + sast_str())
    print("=" * 45)

    try:
        if news and not news["safe"]:
            print("[MAIN] " + symbol + ": NEWS BLOCK - " + news["reason"])
            return {"blocked": "news"}

        data = DataAgent(symbol).fetch(ALL_TF)
        if len(data) == 0:
            print("[MAIN] " + symbol + ": no data - skipping")
            return {"data_empty": True}

        session = SessionAgent(symbol).analyse()
        if not session["tradeable"]:
            print("[MAIN] " + symbol + ": session blocked - " + session["reason"])
            return {"blocked": "session"}

        htf    = HTFAgent(symbol).analyse(data)
        regime = RegimeAgent(symbol).detect(data)
        mtf    = MTFAgent(symbol).analyse(data, htf)
        bias   = BiasAgent(symbol).evaluate(htf, mtf, session, regime)

        if not bias.approved:
            print("[MAIN] " + symbol + ": bias blocked - " + bias.reason)
            return {"blocked": "bias"}

        zone    = ZoneAgent(symbol).analyse(data, bias)
        ltf     = LTFAgent(symbol).analyse(data, mtf, zone)
        trigger = TriggerAgent(symbol).evaluate(ltf, zone, bias)

        if not trigger.fire:
            print("[MAIN] " + symbol + ": no trigger - " + trigger.reason)
            return {"blocked": "trigger"}

        edge  = EdgeAgent(symbol).analyse(trigger, bias, regime)
        score = ScoreAgent(symbol).compute(bias, trigger, regime, edge, session)

        signal_id = build_signal_id(symbol)

        payload = {
            "id":      signal_id,
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

        intelligence_result = IntelligenceAgent(symbol).verdict(payload)
        if not intelligence_result.get("accepted", False):
            reason = intelligence_result.get("reason", "duplicate")
            print("[MAIN] " + symbol + ": signal rejected - " + reason)
            return {"blocked": "duplicate", "reason": reason}

        ArchiveAgent().log(payload)
        PersistenceAgent(symbol).save(intelligence_result, payload)
        NotifierAgent(symbol).send(
            intelligence_result,
            intelligence_result.get("narrative", "")
        )

        try:
            ExecutionAgent(symbol).propose(payload)
        except Exception as e:
            print("[MAIN] " + symbol + ": execution propose error - " + str(e))

        return {"fired": True}

    except Exception as e:
        import traceback
        print("[MAIN] " + symbol + " error: " + str(e))
        print(traceback.format_exc())
        return {"error": str(e)}


if __name__ == "__main__":
    news = NewsAgent().is_safe()
    LifecycleAgent().check_open_signals()

    memory = load_memory() or {}
    context = SymbolContext.from_memory(memory, SYMBOLS)
    chief = ChiefAgent(context)
    decision = chief.decide("full_scan")
    print("[MAIN] Chief scan order: " + ", ".join(decision["symbols"]))
    results = {}
    for symbol in decision["symbols"]:
        results[symbol] = run(symbol, news)

    HealthAgent().check(results)
