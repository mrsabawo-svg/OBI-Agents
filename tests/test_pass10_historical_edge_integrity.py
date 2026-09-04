import copy
from types import SimpleNamespace
from unittest.mock import patch

from agents.archive_agent import ArchiveAgent
from agents.edge_agent import EdgeAgent


def _signal(i, outcome):
    return {
        "id": f"PASS10-CONTROLLED-{i:03d}",
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
            "tags": ["PASS10", "CONTROLLED"],
            "confluence": "CONTROLLED",
        },
        "regime": {"label": "TEST", "confidence": 100},
        "htf": {"bias": "BULLISH", "confidence": 100},
        "bias": {"score": 100, "factors": ["TEST"]},
        "mtf": {"bos": True, "sweep": False, "order_block": "TEST"},
        "status": "CLOSED",
        "outcome": outcome,
        "closed": True,
    }


def test_pass10_historical_edge_reconstructs_from_canonical_archive():
    persisted = {"_archive": []}

    def fake_load():
        return copy.deepcopy(persisted)

    def fake_save(data):
        persisted.clear()
        persisted.update(copy.deepcopy(data))

    with patch("agents.archive_agent.load_memory", side_effect=fake_load), \
         patch("agents.archive_agent.save_memory", side_effect=fake_save), \
         patch("agents.edge_agent.load_memory", side_effect=fake_load):

        archive = ArchiveAgent()
        outcomes = ["TP1"] * 15 + ["SL"] * 5
        for i, outcome in enumerate(outcomes, 1):
            assert archive.log(_signal(i, outcome)) is True

        loaded = fake_load()
        assert len(loaded["_archive"]) == 20
        assert all(t["status"] == "CLOSED" for t in loaded["_archive"])

        edge = EdgeAgent("EURUSD")
        trigger = SimpleNamespace(grade="A", tags=["PASS10", "CONTROLLED"])
        result = edge.analyse(trigger, {}, {"label": "TEST"})

        # 15 wins / 20 closed outcomes = 75% historical win rate.
        assert result.sample_size == 20
        assert result.low_sample is False
        assert result.overall_wr == 75.0
        assert result.symbol_wr == 75.0
        assert result.grade_wr == 75.0
        assert result.regime_wr == 75.0
        assert result.tag_wr == 75.0

        # Fresh process boundary: statistics remain reconstructable from _archive.
        reloaded = fake_load()
        assert len(reloaded["_archive"]) == 20
        assert sum(t["outcome"] == "TP1" for t in reloaded["_archive"]) == 15
        assert sum(t["outcome"] == "SL" for t in reloaded["_archive"]) == 5
