import copy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from agents.archive_agent import ArchiveAgent
from agents.lifecycle_agent import LifecycleAgent
from agents.edge_agent import EdgeAgent


def _signal():
    return {
        "id": "PASS9-CONTROLLED-001",
        "symbol": "EURUSD",
        "trigger": {
            "direction": "BUY",
            "grade": "A",
            "entry": 1.1000,
            "sl": 1.0900,
            "tp1": 1.1100,
            "tp2": 1.1200,
            "tp3": 1.1300,
            "rr": 2.0,
            "tags": ["TEST"],
            "confluence": "CONTROLLED",
        },
        "regime": {"label": "TEST", "confidence": 100},
        "htf": {"bias": "BULLISH", "confidence": 100},
        "bias": {"score": 100, "factors": ["TEST"]},
        "mtf": {"bos": True, "sweep": False, "order_block": "TEST"},
    }


def test_pass9_open_to_closed_uses_same_canonical_memory():
    # Isolated persistence double: load/save behave like a process boundary,
    # while preventing this test from touching the real production Gist.
    persisted = {}

    def fake_load():
        return copy.deepcopy(persisted)

    def fake_save(data):
        persisted.clear()
        persisted.update(copy.deepcopy(data))
        return True

    from agents.lifecycle_agent import SAST
    candle_times = pd.DatetimeIndex([
        SAST.localize(datetime.strptime(value, "%Y-%m-%d %H:%M"))
        for value in ("2026-09-24 06:05", "2026-09-24 06:10", "2026-09-24 06:15")
    ])
    price = pd.DataFrame(
        {
            "High": [1.1110, 1.1210, 1.1310],
            "Low": [1.1010, 1.1110, 1.1210],
        },
        index=candle_times,
    )
    now = SAST.localize(datetime.strptime("2026-09-24 06:20", "%Y-%m-%d %H:%M"))

    with patch("agents.archive_agent.load_memory", side_effect=fake_load), \
         patch("agents.archive_agent.save_memory", side_effect=fake_save), \
         patch("agents.lifecycle_agent.load_memory", side_effect=fake_load), \
         patch("agents.lifecycle_agent.save_memory", side_effect=fake_save), \
         patch("agents.lifecycle_agent.yf.download", return_value=price), \
         patch("agents.edge_agent.load_memory", side_effect=fake_load):

        archive = ArchiveAgent()
        assert archive.log(_signal()) is True

        # The controlled trade opened before the three closed replay candles.
        persisted["_archive"][0]["opened"] = "2026-09-24 06:00 SAST"

        # Fresh read: prove the OPEN signal survives a load boundary.
        reopened = archive.get_history()
        assert len(reopened) == 1
        assert reopened[0]["id"] == "PASS9-CONTROLLED-001"
        assert reopened[0]["status"] == "OPEN"

        # Lifecycle processes three closed candles through TP1, TP2, then TP3.
        LifecycleAgent().check_open_signals(now=now)

        final_state = archive.get_history()
        assert len(final_state) == 1
        assert final_state[0]["id"] == "PASS9-CONTROLLED-001"
        assert final_state[0]["status"] == "CLOSED"
        assert final_state[0]["outcome"] == "TP3"
        assert final_state[0]["tp1_hit"] is True
        assert final_state[0]["tp2_hit"] is True
        assert final_state[0]["tp3_hit"] is True
        assert final_state[0]["closed"] is not None

        # The event has a terminal lifecycle result, but no complete market-data
        # lineage or verified decision provenance. It must not enter trusted
        # historical analytics.
        edge = EdgeAgent("EURUSD")
        trigger = SimpleNamespace(grade="A", tags=["TEST"])
        result = edge.analyse(trigger, {}, {"label": "TEST"})
        assert result.sample_size == 0
        assert result.low_sample is True
        assert result.overall_wr == 50.0
        assert edge.archive_eligibility_summary["excluded_count"] == 1

        # Explicitly verify the persisted archive itself contains the terminal record.
        reloaded = fake_load()
        assert reloaded["_archive"][0]["status"] == "CLOSED"
        assert reloaded["_archive"][0]["outcome"] == "TP3"
