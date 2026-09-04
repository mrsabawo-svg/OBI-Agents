from agents.edge_agent import EdgeAgent
from core.memory import load as load_memory


VALID_OUTCOMES = {"TP1", "TP2", "TP3", "SL"}


def test_pass11_production_archive_edge_validity():
    memory = load_memory() or {}
    archive = memory.get("_archive", [])

    closed = [
        trade for trade in archive
        if trade.get("status") == "CLOSED"
        and trade.get("outcome") in VALID_OUTCOMES
    ]

    malformed = [
        trade for trade in closed
        if not trade.get("id")
        or not trade.get("symbol")
        or not trade.get("outcome")
        or trade.get("pnl_pips") is None
    ]
    ids = [trade.get("id") for trade in closed]
    duplicate_ids = len(ids) - len(set(ids))

    print(f"[PASS11] archive_entries={len(archive)}")
    print(f"[PASS11] valid_closed={len(closed)}")
    print(f"[PASS11] malformed_closed={len(malformed)}")
    print(f"[PASS11] duplicate_closed_ids={duplicate_ids}")

    assert len(closed) >= 20, (
        f"Production archive has only {len(closed)} valid closed outcomes; "
        "minimum production EdgeAgent sample is 20."
    )
    assert not malformed, "Production archive contains malformed closed outcomes"
    assert duplicate_ids == 0, "Production archive contains duplicate closed signal IDs"

    result = EdgeAgent("EURUSD").analyse({}, {}, {})

    print(f"[PASS11] edge_sample_size={result.sample_size}")
    print(f"[PASS11] edge_low_sample={result.low_sample}")
    print(f"[PASS11] edge_overall_wr={result.overall_wr}")

    assert result.sample_size >= 20
    assert result.low_sample is False

    # Pass 11 deliberately does not assert a target win rate. The observed
    # production result is evidence, not a synthetic expected value.
