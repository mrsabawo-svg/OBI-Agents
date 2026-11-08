"""Immutable, evidence-backed lineage for OBI decision-agent evaluations.

Formula specifications describe the current implementation. They do not
assert that the formulas are statistically validated or recommended.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from importlib import metadata as importlib_metadata
import hashlib
import json
import math
from pathlib import Path
import platform
from typing import Any


DECISION_LINEAGE_SCHEMA_VERSION = "obi-decision-lineage-v1"
PIPELINE_VERSION = "4.2"

AGENT_ORDER = (
    "Session",
    "HTF",
    "Regime",
    "MTF",
    "Bias",
    "Zone",
    "LTF",
    "Trigger",
    "Edge",
    "Score",
)

AGENT_SPECS = {
    "Session": {
        "agent_version": "1.0.1",
        "formula_id": "session-sast-eligibility",
        "formula_version": "1.0.0",
        "formula": (
            "Synthetics are always tradeable; crypto is always tradeable except "
            "the session clock is still recorded. Other markets are blocked on "
            "weekends and at SAST hour 23, tradeable in preferred kill zones, "
            "or tradeable during SAST hours 01-22."
        ),
        "parameters": {
            "timezone": "Africa/Johannesburg",
            "kill_zones_sast": {
                "Asian Session": [1, 3],
                "London Open": [9, 11],
                "New York Open": [15, 17],
            },
            "kill_zone_end_hour_exclusive": True,
            "dead_hour": 23,
            "general_tradeable_hours_inclusive": [1, 22],
            "crypto_symbols": ["BTCUSD", "ETHUSD", "SOLUSD"],
            "synthetic_symbols": [
                "PAIN_999", "GAIN_999", "PAIN_1200", "GAIN_1200", "SFX_VOL_75"
            ],
        },
        "inputs": ["instrument_id", "captured SAST datetime", "kill-zone clock"],
        "outputs": ["tradeable", "reason", "kill_zone", "zone_name", "peak", "evaluated_at_sast"],
        "market_data_timeframes": [],
        "upstream_agents": [],
        "source_files": ["agents/session_agent.py", "core/utils.py"],
        "known_limitations": [
            "Synthetic 24/7 eligibility is hard-coded and is not broker-session evidence.",
            "peak mirrors any globally active kill zone, even when that zone is not preferred for the symbol.",
            "London Close is listed as a preferred session but has no kill-zone window in core.utils.",
        ],
    },
    "HTF": {
        "agent_version": "1.0.0",
        "formula_id": "htf-ema-and-extreme-directional-vote",
        "formula_version": "1.0.0",
        "formula": (
            "Use 4h, falling back to 1h. Five binary votes are price above EMA20, "
            "price above EMA50, EMA20 above EMA50, last high above first high in "
            "the last ten bars, and last low above first low in the last ten bars. "
            "Bullish or bearish requires at least four matching votes; confidence "
            "is the larger vote count divided by five."
        ),
        "parameters": {
            "timeframe_priority": ["4h", "1h"],
            "minimum_bars": 5,
            "ema_periods": [20, 50],
            "structure_lookback": 10,
            "directional_threshold": 4,
            "vote_count": 5,
            "confidence_denominator": 5,
            "missing_ema_fallback": "Close",
        },
        "inputs": ["Close", "High", "Low", "EMA_20", "EMA_50"],
        "outputs": ["bias", "confidence", "bull_score", "bear_score"],
        "market_data_timeframes": ["4h", "1h"],
        "upstream_agents": [],
        "source_files": ["agents/htf_agent.py"],
        "known_limitations": [
            "Structure compares the last bar with the first bar of the lookback; it does not identify swing pivots.",
            "Missing EMA columns fall back to Close, which changes the vote semantics.",
        ],
    },
    "Regime": {
        "agent_version": "1.0.0",
        "formula_id": "hmm-volatility-regime-classifier",
        "formula_version": "1.0.0",
        "formula": (
            "Fit a three-state Gaussian HMM to log returns, rolling return "
            "volatility, high-low range divided by close, and rolling mean "
            "momentum. Try diagonal, full, then spherical covariance. Map the "
            "lowest mean-volatility state to RANGING, middle to TRENDING, and "
            "highest to VOLATILE; confidence is the current-state posterior."
        ),
        "parameters": {
            "timeframe_priority": ["1h", "4h"],
            "minimum_bars": 50,
            "minimum_feature_rows": 30,
            "log_return_epsilon": 1e-9,
            "volatility_window": 6,
            "momentum_window": 4,
            "range_feature": "(High-Low)/(Close+1e-9)",
            "components": 3,
            "covariance_types_in_fallback_order": ["diag", "full", "spherical"],
            "iterations": 100,
            "random_state": 42,
            "state_mapping": "sort fitted state means by volatility feature",
        },
        "inputs": ["Close", "High", "Low"],
        "outputs": ["state", "label", "confidence", "raw_state"],
        "market_data_timeframes": ["1h", "4h"],
        "upstream_agents": [],
        "source_files": ["agents/regime_agent.py"],
        "known_limitations": [
            "The middle-volatility state is labelled TRENDING without a separate trend-direction test.",
            "The model is refitted on each evaluation window.",
        ],
    },
    "MTF": {
        "agent_version": "1.0.0",
        "formula_id": "mtf-directional-structure-confluence",
        "formula_version": "1.0.0",
        "formula": (
            "Use 1h, falling back to 15m, and follow HTF direction. Evaluate "
            "range midpoint, BOS against a seven-bar slice, a sweep against the "
            "previous nine bars, a candle-based order-block test at offset -3, "
            "and EMA20/EMA50 alignment. Direction is aligned when at least one "
            "of the five directional factors passes."
        ),
        "parameters": {
            "timeframe_priority": ["1h", "15m"],
            "minimum_bars": 20,
            "range_lookback": 20,
            "bos_slice": [-10, -3],
            "liquidity_sweep_reference_slice": [-10, -1],
            "order_block_anchor_offset": -3,
            "ema_periods": [20, 50],
            "alignment_minimum_factors": 1,
            "factor_count": 5,
        },
        "inputs": ["HTF.bias", "Open", "High", "Low", "Close", "EMA_20", "EMA_50"],
        "outputs": ["aligned", "bos", "sweep", "order_block", "confluence", "direction"],
        "market_data_timeframes": ["1h", "15m"],
        "upstream_agents": ["HTF"],
        "source_files": ["agents/mtf_agent.py"],
        "known_limitations": [
            "A single passing factor is sufficient for aligned=True.",
            "The exported bos and order_block flags combine bullish and bearish events, even when HTF direction is one-sided.",
        ],
    },
    "Bias": {
        "agent_version": "4.3.0",
        "formula_id": "bias-seven-factor-filter-v4-3",
        "formula_version": "4.3.0",
        "formula": (
            "Require a tradeable session, non-neutral HTF bias, HTF confidence "
            "at least 0.50, aligned MTF, and no VOLATILE regime above 0.70 "
            "confidence. Count seven factors; require at least two. Grade by "
            "factor count and filter Grade C unless BOS or Kill Zone is present."
        ),
        "parameters": {
            "htf_confidence_minimum": 0.50,
            "htf_clear_factor_threshold": 0.60,
            "volatile_block_confidence": 0.70,
            "minimum_factor_score": 2,
            "factor_count": 7,
            "grade_thresholds": {"A+": 6, "A": 5, "B": 4, "C": 3},
            "grade_c_requires_any": ["MTF BOS confirmed", "Kill zone active"],
            "grade_c_embedded_sample_claims": {
                "without_bos_or_kill_zone": {"win_rate_pct": 21.7, "trades": 46},
                "with_bos_or_kill_zone": {"win_rate_pct": 38.7, "trades": 31},
            },
        },
        "inputs": [
            "HTF.bias", "HTF.confidence", "MTF.aligned", "MTF.bos",
            "MTF.sweep", "MTF.order_block", "MTF.direction",
            "Session.tradeable", "Session.kill_zone", "Regime.label", "Regime.confidence",
        ],
        "outputs": ["approved", "direction", "grade", "score", "factors", "regime", "reason"],
        "market_data_timeframes": [],
        "upstream_agents": ["HTF", "MTF", "Session", "Regime"],
        "source_files": ["agents/bias_agent.py", "core/models.py"],
        "known_limitations": [
            "Scores below two are blocked; Grade D is therefore reachable only at exactly two factors.",
            "The embedded Grade C win-rate claims do not identify a dataset or report version.",
        ],
    },
    "Zone": {
        "agent_version": "1.0.0",
        "formula_id": "premium-discount-orderblock-htf-targets",
        "formula_version": "1.0.0",
        "formula": (
            "Use 1h/15m for local range, midpoint, support/resistance and simple "
            "order-block candidates; use 4h/1h for HTF swing targets. A 0.1% "
            "price buffer filters target levels. Select the median sorted level "
            "above/below price; fall back to 1h history when HTF candidates are absent."
        ),
        "parameters": {
            "htf_timeframe_priority": ["4h", "1h"],
            "mtf_timeframe_priority": ["1h", "15m"],
            "swing_lookback": 50,
            "support_resistance_lookback": 20,
            "order_block_scan_range": [-15, -2],
            "htf_swing_lookback": 30,
            "target_price_buffer_fraction": 0.001,
            "fallback_1h_target_lookback": 200,
            "target_selection": "median of sorted eligible highs/lows",
            "zone_alignment": "BUY below midpoint; SELL above midpoint",
        },
        "inputs": ["Bias.direction", "Open", "High", "Low", "Close"],
        "outputs": [
            "current_price", "swing_high", "swing_low", "midpoint",
            "in_discount", "in_premium", "resistance_1h", "support_1h",
            "ob_bull", "ob_bear", "htf_swing_high", "htf_swing_low",
            "htf_tp_bull", "htf_tp_bear", "zone_aligned",
            "dist_to_resistance", "dist_to_support",
        ],
        "market_data_timeframes": ["4h", "1h", "15m"],
        "upstream_agents": ["Bias"],
        "source_files": ["agents/zone_agent.py", "core/models.py"],
        "known_limitations": [
            "Target selection uses a median eligible level, not the nearest level.",
            "Order-block identification is a one-candle heuristic.",
            "Short MTF windows can fail the order-block scan and fall back to the default zone result.",
            "Zone alignment is advisory; TriggerAgent does not require it.",
        ],
    },
    "LTF": {
        "agent_version": "1.0.0",
        "formula_id": "ltf-entry-fvg-ob-momentum-atr",
        "formula_version": "1.0.0",
        "formula": (
            "Use 5m, falling back to 15m. Detect engulfing or order-block retest "
            "as the trigger, three-candle FVG, three-candle momentum, and MTF "
            "sweep. Confluence is the sum of those four flags. Use ATR*1.2 for "
            "the stop, zone/HTF levels for targets with ATR fallbacks, and TP1 "
            "distance divided by stop distance for RR."
        ),
        "parameters": {
            "timeframe_priority": ["5m", "15m"],
            "minimum_bars": 20,
            "fvg_lookback_offset": -3,
            "order_block_retest_offset": -2,
            "momentum_bars": 3,
            "confluence_components": ["trigger", "fvg", "momentum", "mtf_sweep"],
            "atr_stop_multiple": 1.2,
            "atr_fallback_fraction_of_entry": 0.001,
            "atr_column_selection": "first column whose name contains ATR",
            "fallback_target_multiples": [3, 6, 9],
            "price_rounding_decimals": 5,
            "rr_rounding_decimals": 2,
            "tp3_extension_fraction": 0.5,
            "rr_target": "abs(TP1-entry)/abs(entry-SL)",
        },
        "inputs": [
            "MTF.direction", "MTF.sweep", "Zone.resistance_1h", "Zone.support_1h",
            "Zone.htf_tp_bull", "Zone.htf_tp_bear", "Zone.htf_swing_high",
            "Zone.htf_swing_low", "Open", "High", "Low", "Close", "first ATR column",
        ],
        "outputs": [
            "trigger", "fvg", "momentum", "confluence", "entry", "sl",
            "tp1", "tp2", "tp3", "rr", "atr", "direction", "valid",
        ],
        "market_data_timeframes": ["5m", "15m"],
        "upstream_agents": ["MTF", "Zone"],
        "source_files": ["agents/ltf_agent.py"],
        "known_limitations": [
            "valid is trigger and confluence>=1; because trigger is itself counted in confluence, the confluence test is redundant.",
        ],
    },
    "Trigger": {
        "agent_version": "1.0.0",
        "formula_id": "trigger-final-entry-confirmation",
        "formula_version": "1.0.0",
        "formula": (
            "Fire only when LTF valid is true, RR is at least 1.2, and bias "
            "direction is non-neutral. Zone alignment adds one confluence point "
            "and affects tags but does not block the trigger."
        ),
        "parameters": {
            "minimum_rr": 1.2,
            "zone_alignment_confluence_bonus": 1,
            "grade_thresholds": {
                "A+": {"confluence_min": 5, "rr_min": 3.0},
                "A": {"confluence_min": 4, "rr_min": 2.0},
                "B": {"confluence_min": 3, "rr_min": 1.5},
                "C": "otherwise",
            },
            "tags": ["FVG", "Momentum", "OB", "Discount", "Premium"],
        },
        "inputs": [
            "LTF.valid", "LTF.rr", "LTF.confluence", "LTF.entry", "LTF.sl",
            "LTF.tp1", "LTF.tp2", "LTF.tp3", "LTF.fvg", "LTF.momentum",
            "Zone.zone_aligned", "Zone.ob_bull", "Zone.ob_bear",
            "Zone.in_discount", "Zone.in_premium", "Bias.direction",
        ],
        "outputs": [
            "fire", "direction", "grade", "entry", "sl", "tp1", "tp2",
            "tp3", "rr", "confluence", "tags", "reason",
        ],
        "market_data_timeframes": [],
        "upstream_agents": ["LTF", "Zone", "Bias"],
        "source_files": ["agents/trigger_agent.py", "core/models.py"],
        "known_limitations": [
            "Zone misalignment is explicitly allowed to proceed.",
        ],
    },
    "Edge": {
        "agent_version": "1.0.0",
        "formula_id": "closed-archive-historical-edge",
        "formula_version": "1.0.0",
        "formula": (
            "Read closed archive records excluding EXPIRED. Below 20 records, "
            "return neutral 50% metrics and low_sample=True. Otherwise calculate "
            "symbol, regime, tag and overall win rates; win outcomes are TP1/TP2/TP3. "
            "grade_wr is assigned overall_wr to avoid circular grade dependence."
        ),
        "parameters": {
            "minimum_sample": 20,
            "eligible_status": "CLOSED",
            "excluded_outcome": "EXPIRED",
            "winning_outcomes": ["TP1", "TP2", "TP3"],
            "grade_wr_source": "overall_wr",
            "tag_win_rate_used_by_score": False,
            "minimum_sample_scope": "global closed archive, not per subgroup",
            "low_sample_defaults_pct": 50.0,
        },
        "inputs": ["closed signal archive", "Trigger.tags", "Bias", "Regime.label"],
        "outputs": [
            "symbol_wr", "grade_wr", "regime_wr", "tag_wr",
            "overall_wr", "sample_size", "low_sample",
        ],
        "market_data_timeframes": [],
        "upstream_agents": ["Trigger", "Bias", "Regime"],
        "source_files": ["agents/edge_agent.py", "core/memory.py", "core/models.py"],
        "known_limitations": [
            "tag_wr is calculated but is not used in ScoreAgent's edge formula.",
            "The minimum sample applies to the global archive, not each symbol/regime/tag subgroup; empty subgroups return 50%.",
            "The archive snapshot must be captured separately to identify the exact historical input set.",
        ],
    },
    "Score": {
        "agent_version": "4.3.0",
        "formula_id": "weighted-confidence-score-v4-3",
        "formula_version": "4.3.0",
        "formula": (
            "Confidence is a weighted sum of normalized bias, trigger, edge, "
            "regime and session scores. Add five points for Kill Zone and subtract "
            "eight for low sample. Clamp to 0-100, then grade and assign risk."
        ),
        "parameters": {
            "weights": {
                "bias": 0.28,
                "trigger": 0.28,
                "edge": 0.24,
                "regime": 0.10,
                "session": 0.10,
            },
            "bias_score": "min(1, bias.score/7)",
            "trigger_score": "0.6*min(1,confluence/5)+0.4*min(1,rr/3)",
            "regime_base": {"TRENDING": 1.0, "RANGING": 0.7, "VOLATILE": 0.1, "other": 0.5},
            "edge_low_sample_floor": 0.4,
            "edge_normal_weights": {"symbol_wr": 0.40, "grade_wr": 0.35, "regime_wr": 0.25},
            "session_score": {"kill_zone": 1.0, "tradeable": 0.6, "otherwise": 0.0},
            "kill_zone_bonus_points": 5,
            "low_sample_penalty_points": 8,
            "grade_thresholds": {"A+": 85, "A": 75, "B": 65, "C": 55},
            "risk_thresholds": {
                "LOW": {"confidence_min": 75, "rr_min": 2.0},
                "MEDIUM": {"confidence_min": 60, "rr_min": 1.5},
                "HIGH": "otherwise",
            },
        },
        "inputs": [
            "Bias.score", "Trigger.confluence", "Trigger.rr",
            "Regime.label", "Regime.confidence", "Edge.low_sample",
            "Edge.symbol_wr", "Edge.grade_wr", "Edge.regime_wr",
            "Session.kill_zone", "Session.tradeable",
        ],
        "outputs": [
            "confidence", "grade", "risk", "bias_score", "trigger_score",
            "regime_score", "edge_score", "session_score",
        ],
        "market_data_timeframes": [],
        "upstream_agents": ["Bias", "Trigger", "Regime", "Edge", "Session"],
        "source_files": ["agents/score_agent.py", "core/models.py"],
        "known_limitations": [
            "The tag win rate is not included in the edge score.",
            "The component weights sum to one before the separate bonus and penalty adjustments.",
        ],
    },
}

ROOT_SOURCE_FILES = [
    "main.py",
    "core/decision_lineage.py",
    "agents/intelligence_agent.py",
    "agents/archive_agent.py",
    "agents/lifecycle_agent.py",
    "agents/tracker_agent.py",
    "agents/signal_reviewer.py",
    "agents/persistence_agent.py",
    "agents/notifier_agent.py",
    "agents/execution_agent.py",
    "agents/data_agent.py",
    "core/data_lineage.py",
    "core/feature_lineage.py",
    "core/history_completeness.py",
    "core/mt5_connector.py",
    "core/canonical_data_store.py",
    "core/raw_observation_store.py",
    "core/instrument_registry.py",
    "core/instrument_registry_store.py",
    "mt5_bridge.py",
]


def _normalise(value: Any) -> Any:
    if is_dataclass(value):
        return _normalise(asdict(value))
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {
            str(key): _normalise(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    if isinstance(value, set):
        return sorted((_normalise(item) for item in value), key=lambda item: str(item))
    if type(value).__name__ == "NAType":
        return None
    if hasattr(value, "item"):
        try:
            return _normalise(value.item())
        except (TypeError, ValueError):
            pass
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            pass
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _normalise(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _sha256(value: Any) -> str:
    payload = value if isinstance(value, (str, bytes)) else _canonical_json(value)
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _frame_fingerprint(frame: Any) -> dict | None:
    if frame is None:
        return None
    if not hasattr(frame, "itertuples") or not hasattr(frame, "columns"):
        return {"sha256": _sha256(frame), "row_count": None, "columns": []}
    columns = [str(column) for column in frame.columns]
    index = [_normalise(value) for value in frame.index.tolist()]
    rows = [
        [_normalise(value) for value in row]
        for row in frame.itertuples(index=False, name=None)
    ]
    material = {"columns": columns, "index": index, "rows": rows}
    return {
        "sha256": _sha256(material),
        "row_count": len(frame),
        "columns": columns,
        "index_start": index[0] if index else None,
        "index_end": index[-1] if index else None,
    }


def _selected_timeframes(spec: dict, market_data: dict) -> dict:
    parameters = spec.get("parameters", {})

    def first_available(priority):
        for timeframe in priority:
            frame = market_data.get(timeframe)
            if frame is not None and not getattr(frame, "empty", False):
                return timeframe
        return None

    if "timeframe_priority" in parameters:
        return {"selected": first_available(parameters["timeframe_priority"])}
    if "htf_timeframe_priority" in parameters or "mtf_timeframe_priority" in parameters:
        return {
            "htf": first_available(parameters.get("htf_timeframe_priority", [])),
            "mtf": first_available(parameters.get("mtf_timeframe_priority", [])),
        }
    return {}


def _package_version(package: str) -> str | None:
    try:
        return importlib_metadata.version(package)
    except importlib_metadata.PackageNotFoundError:
        return None


def _runtime_versions() -> dict:
    return {
        "python": platform.python_version(),
        "numpy": _package_version("numpy"),
        "pandas": _package_version("pandas"),
        "hmmlearn": _package_version("hmmlearn"),
        "scipy": _package_version("scipy"),
        "pytz": _package_version("pytz"),
    }


def _source_hashes(root: Path, relative_paths: set[str]) -> dict:
    hashes = {}
    for relative_path in sorted(relative_paths):
        path = root / relative_path
        if not path.is_file():
            raise FileNotFoundError(
                f"decision lineage source file is missing: {relative_path}"
            )
        hashes[relative_path] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def build_decision_lineage(
    symbol: str,
    market_data: dict,
    agent_outputs: dict,
    data_lineage: dict | None,
    external_inputs: dict | None = None,
    evaluated_at: datetime | None = None,
) -> dict:
    """Build a self-verifying manifest for one fired OBI decision."""
    missing_agents = [name for name in AGENT_ORDER if name not in agent_outputs]
    if missing_agents:
        raise ValueError("missing decision outputs: " + ", ".join(missing_agents))

    now = evaluated_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("evaluated_at must be timezone-aware")
    evaluated_at_iso = now.astimezone(timezone.utc).isoformat()
    external_inputs = external_inputs or {}

    market_fingerprints = {
        timeframe: _frame_fingerprint(frame)
        for timeframe, frame in sorted(market_data.items())
    }
    data_lineage_hash = _sha256(data_lineage or {})
    root = Path(__file__).resolve().parents[1]

    all_source_files = set(ROOT_SOURCE_FILES)
    for spec in AGENT_SPECS.values():
        all_source_files.update(spec["source_files"])
    source_hashes = _source_hashes(root, all_source_files)

    specs = {}
    traces = {}
    output_hashes = {}
    warnings = []

    for agent_name in AGENT_ORDER:
        spec = AGENT_SPECS[agent_name]
        spec_hash = _sha256(spec)
        implementation_hash = _sha256({
            path: source_hashes[path] for path in spec["source_files"]
        })
        output = _normalise(agent_outputs[agent_name])
        output_hash = _sha256(output)
        output_hashes[agent_name] = output_hash

        market_refs = {
            timeframe: (
                market_fingerprints.get(timeframe, {}).get("sha256")
                if market_fingerprints.get(timeframe) else None
            )
            for timeframe in spec["market_data_timeframes"]
        }
        upstream_refs = {
            upstream: output_hashes[upstream]
            for upstream in spec["upstream_agents"]
        }
        external_refs = _normalise(external_inputs.get(agent_name, {}))
        if agent_name == "Session":
            external_refs = {
                **(external_refs if isinstance(external_refs, dict) else {}),
                "evaluation_clock_utc": evaluated_at_iso,
                "symbol": symbol,
            }

        selected_timeframes = _selected_timeframes(spec, market_data)
        unavailable_candidates = [
            timeframe for timeframe, digest in market_refs.items() if digest is None
        ]
        input_snapshot = {
            "symbol": symbol,
            "market_data": market_refs,
            "selected_timeframes": selected_timeframes,
            "unavailable_candidate_timeframes": unavailable_candidates,
            "upstream_outputs": upstream_refs,
            "external_inputs": external_refs,
        }
        if "selected" in selected_timeframes and selected_timeframes["selected"] is None:
            warnings.append(f"{agent_name}: no usable candidate timeframe was available")
        if "mtf" in selected_timeframes and selected_timeframes["mtf"] is None:
            warnings.append("Zone: no usable MTF timeframe was available")
        if "htf" in selected_timeframes and selected_timeframes["htf"] is None:
            warnings.append("Zone: HTF context unavailable; MTF swing fallback is in use")
        if agent_name == "Edge":
            archive_hash = (
                external_refs.get("archive_snapshot_sha256")
                if isinstance(external_refs, dict) else None
            )
            if not archive_hash:
                warnings.append("Edge: closed archive snapshot hash unavailable")

        specs[agent_name] = {
            **_normalise(spec),
            "formula_spec_sha256": spec_hash,
        }
        traces[agent_name] = {
            "formula_spec_sha256": spec_hash,
            "implementation_sha256": implementation_hash,
            "input_snapshot": input_snapshot,
            "input_snapshot_sha256": _sha256(input_snapshot),
            "data_lineage_sha256": data_lineage_hash,
            "output": output,
            "output_sha256": output_hash,
        }

    data_lineage_status = (data_lineage or {}).get("status", "unavailable")
    if data_lineage_status != "complete":
        warnings.append("pipeline data lineage is " + str(data_lineage_status))

    manifest = {
        "schema_version": DECISION_LINEAGE_SCHEMA_VERSION,
        "status": "complete",
        "provenance_status": "partial" if warnings else "complete",
        "pipeline_version": PIPELINE_VERSION,
        "instrument_id": symbol,
        "evaluated_at": evaluated_at_iso,
        "agent_order": list(AGENT_ORDER),
        "data_lineage_status": data_lineage_status,
        "data_lineage_sha256": data_lineage_hash,
        "market_data": market_fingerprints,
        "runtime_versions": _runtime_versions(),
        "source_hashes": source_hashes,
        "agent_specs": specs,
        "agent_traces": traces,
        "warnings": warnings,
    }
    manifest["manifest_content_size_bytes"] = len(_canonical_json(manifest).encode("utf-8"))
    manifest["manifest_sha256"] = _sha256(manifest)
    return manifest


def verify_decision_lineage(manifest: dict) -> bool:
    """Verify the manifest's content hash without trusting its stored digest."""
    if not isinstance(manifest, dict):
        return False
    if manifest.get("schema_version") != DECISION_LINEAGE_SCHEMA_VERSION:
        return False
    supplied_hash = manifest.get("manifest_sha256")
    if not isinstance(supplied_hash, str) or len(supplied_hash) != 64:
        return False
    material = dict(manifest)
    material.pop("manifest_sha256", None)
    return supplied_hash == _sha256(material)
