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
from agents.tracker_agent      import check_outcome
from agents.session_agent      import SessionAgent
from agents.bias_agent         import BiasAgent
from agents.zone_agent         import ZoneAgent
from agents.trigger_agent      import TriggerAgent
from agents.regime_agent       import RegimeAgent
from agents.health_agent       import HealthAgent
from agents.lifecycle_agent    import LifecycleAgent
from agents.edge_agent         import EdgeAgent
from agents.score_agent        import ScoreAgent
from agents.intelligence_agent import IntelligenceAgent
from agents.execution_agent    import ExecutionAgent
from core.utils                import now_sast, sast_str
from core.data_lineage          import build_data_lineage
from core.decision_lineage      import build_decision_lineage

SYMBOLS = [
    "XAUUSD", "EURUSD", "USDJPY", "GBPJPY", "GBPUSD",
    "BTCUSD", "ETHUSD", "SOLUSD", "NASDAQ",
    "PAIN_999", "GAIN_999", "PAIN_1200", "GAIN_1200", "SFX_VOL_75"
]
ALL_TF  = ["4h", "1h", "15m", "5m"]


def build_signal_id(symbol: str) -> str:
    """Create a unique, human-readable identity for one fired signal event."""
    normalized_symbol = str(symbol).strip().upper()
    if not normalized_symbol:
        raise ValueError("symbol must be a non-empty string")
    stamp = now_sast().strftime("%Y%m%d%H%M%S")
    return normalized_symbol + "_" + stamp + "_" + uuid.uuid4().hex[:16]

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

        data_lineage = build_data_lineage(
            data,
            instrument_id=symbol,
            expected_timeframes=ALL_TF,
        )

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

        edge_agent = EdgeAgent(symbol)
        edge = edge_agent.analyse(trigger, bias, regime)
        score = ScoreAgent(symbol).compute(bias, trigger, regime, edge, session)

        decision_lineage = build_decision_lineage(
            symbol=symbol,
            market_data=data,
            agent_outputs={
                "Session": session,
                "HTF": htf,
                "Regime": regime,
                "MTF": mtf,
                "Bias": bias,
                "Zone": zone,
                "LTF": ltf,
                "Trigger": trigger,
                "Edge": edge,
                "Score": score,
            },
            data_lineage=data_lineage,
            external_inputs={
                "Session": {
                    "evaluated_at_sast": session.get("evaluated_at_sast"),
                },
                "Edge": {
                    "archive_snapshot_sha256": edge_agent.archive_snapshot_sha256,
                },
            },
        )

        payload = {
            "id":      build_signal_id(symbol),
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
            "data_lineage": data_lineage,
            "decision_lineage": decision_lineage,
        }

        intelligence_result = IntelligenceAgent(symbol).verdict(payload)
        if not intelligence_result:
            return {"blocked": "duplicate_or_archive_persistence"}

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

    results = {}
    for symbol in SYMBOLS:
        results[symbol] = run(symbol, news)

    HealthAgent().check(results)
