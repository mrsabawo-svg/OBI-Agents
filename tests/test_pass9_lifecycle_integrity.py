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

    price = pd.DataFrame({"Close": [1.1100]})

    with patch("agents.archive_agent.load_memory", side_effect=fake_load), \
         patch("agents.archive_agent.save_memory", side_effect=fake_save), \
         patch("agents.lifecycle_agent.load_memory", side_effect=fake_load), \
         patch("agents.lifecycle_agent.save_memory", side_effect=fake_save), \
         patch("agents.lifecycle_agent.yf.download", return_value=price), \
         patch("agents.edge_agent.load_memory", side_effect=fake_load):

        archive = ArchiveAgent()
        assert archive.log(_signal()) is True

        # Fresh read: prove the OPEN signal survives a load boundary.
        reopened = archive.get_history()
        assert len(reopened) == 1
        assert reopened[0]["id"] == "PASS9-CONTROLLED-001"
        assert reopened[0]["status"] == "OPEN"

        # Lifecycle closes the same persisted record using market data from the
        # controlled fixture: BUY price reaches TP1.
        LifecycleAgent().check_open_signals()

        final_state = archive.get_history()
        assert len(final_state) == 1
        assert final_state[0]["id"] == "PASS9-CONTROLLED-001"
        assert final_state[0]["status"] == "CLOSED"
        assert final_state[0]["outcome"] == "TP1"
        assert final_state[0]["closed"] is not None

        # EdgeAgent reads the same _archive and reconstructs the one closed sample.
        edge = EdgeAgent("EURUSD")
        trigger = SimpleNamespace(grade="A", tags=["TEST"])
        result = edge.analyse(trigger, {}, {"label": "TEST"})
        assert result.sample_size == 1
        assert result.overall_wr == 100.0

        # Explicitly verify the persisted archive itself contains the terminal record.
        reloaded = fake_load()
        assert reloaded["_archive"][0]["status"] == "CLOSED"
        assert reloaded["_archive"][0]["outcome"] == "TP1"
