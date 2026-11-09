# OBI Decision Lineage v1

## Purpose

Every fired signal should preserve enough evidence to identify the exact decision logic that produced it. This includes the formula identity and version, parameters, declared inputs and outputs, source-code hashes, actual input/output hashes, runtime dependency versions, and the upstream-agent chain.

The registry in `core/decision_lineage.py` is the machine-readable source of truth. This document explains the contract and the current baseline.

## Versioning policy

- **Formula version** changes when a calculation, threshold, parameter, input interpretation, or output meaning changes.
- **Agent version** changes when the agent implementation or interface changes without changing the declared formula semantics.
- **Implementation hashes** are calculated from the actual source files for every run. They identify the exact implementation even if a version bump is accidentally missed.
- **Formula-spec hashes** identify the declared formula, parameters, inputs, outputs, and limitations.
- Never edit an old formula specification in place to make historical records appear current. Add a new formula version and retain the previous specification.
- Any formula change requires tests that demonstrate the old/new behavior and a deliberate version increment.

## What each archived signal records

The `obi-decision-lineage-v1` manifest contains:

- Pipeline and schema versions, instrument ID, and evaluation time.
- The data-lineage manifest hash and per-timeframe DataFrame hashes, row counts, columns, and index boundaries.
- Runtime versions for Python, NumPy, pandas, hmmlearn, SciPy, and pytz.
- Unique source-file SHA-256 hashes, including the orchestration and formula implementation files.
- A formula specification for each agent: agent version, formula ID/version, formula description, parameters, inputs, outputs, source files, and known limitations.
- A trace for each agent: formula-spec hash, implementation hash, selected timeframe(s), upstream output hashes, external input references, exact output snapshot, and input/output hashes.
- A manifest hash verified by ArchiveAgent before the lineage is accepted into the signal archive.

The EdgeAgent trace also references a hash of the exact closed-archive fields used by its historical win-rate calculations. The SessionAgent records the SAST timestamp used for its session decision.

`status=complete` means the versioned manifest was built and its contents are self-verifying. `provenance_status` separately reports whether all expected input references were available. A complete manifest can therefore carry partial-provenance warnings; these are not silently discarded.

## Current baseline formula versions

| Agent | Formula ID | Formula version |
|---|---|---:|
| Session | `session-sast-eligibility` | 1.0.0 |
| HTF | `htf-ema-and-extreme-directional-vote` | 1.0.0 |
| Regime | `hmm-volatility-regime-classifier` | 1.0.0 |
| MTF | `mtf-directional-structure-confluence` | 1.0.0 |
| Bias | `bias-seven-factor-filter-v4-3` | 4.3.0 |
| Zone | `premium-discount-orderblock-htf-targets` | 1.0.0 |
| LTF | `ltf-entry-fvg-ob-momentum-atr` | 1.0.0 |
| Trigger | `trigger-final-entry-confirmation` | 1.0.0 |
| Edge | `closed-archive-historical-edge` | 1.0.0 |
| Score | `weighted-confidence-score-v4-3` | 4.3.0 |

These are baseline identifiers assigned to the behavior currently present in the repository. They are not a claim that the formulas have been independently validated or are optimal.

## Baseline limitations preserved for review

The versioned specification records, rather than silently repairs, the following existing behavior:

1. MTF alignment passes when one of five directional factors passes.
2. MTF's exported BOS and order-block flags combine bullish and bearish events, although its directional alignment factors are side-specific.
3. LTF validity is effectively trigger-only because the trigger itself contributes to the confluence minimum.
4. Regime maps the middle-volatility HMM state to TRENDING without a separate trend-direction test.
5. Bias Grade D is reachable only at exactly two factors; fewer than two are blocked. The embedded Grade C win-rate claims do not identify a dataset/report version.
6. Zone alignment is advisory; TriggerAgent permits a misaligned zone.
7. EdgeAgent's minimum sample applies to the global closed archive, not each symbol/regime/tag subgroup. Tag win rate is calculated but not used by ScoreAgent.
8. SessionAgent hard-codes synthetic 24/7 eligibility. Its `peak` flag mirrors any globally active kill zone, even if that zone is not preferred for the instrument. The configured London Close label has no corresponding kill-zone window.
9. The broker-history session model is separate from SessionAgent's trading-eligibility rules. A synthetic label must not be treated as proof of a broker's trading calendar.

These issues should be prioritized in a separate formula-review and calibration workstream. Versioning must not be used to imply that the current logic is sound.

## Scope

The manifest is created for fired signals after ScoreAgent runs and is persisted in the canonical signal archive. Blocked/no-trigger evaluations are not yet written to a separate decision-event archive.

Each signal now carries the formula specifications as well as the hashes, so the archive grows by several kilobytes per signal. Retention/rotation of the persistent memory archive remains a separate operational requirement.

This is decision provenance, not deterministic replay. The source, input, output, and dependency hashes identify the calculation context; a future replay facility would additionally need a controlled market-data snapshot loader and deterministic execution environment.
