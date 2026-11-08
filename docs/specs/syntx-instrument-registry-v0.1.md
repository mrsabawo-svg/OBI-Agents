# SyntX Instrument Registry and Market-Data Integrity Contract

**Document ID:** OBI-DATA-SPEC-001  
**Version:** 0.1.0  
**Status:** Proposed — review required before implementation  
**Created:** 2026-11-08  
**Applies to:** OBI market-data acquisition, instrument identity, synthetic-instrument configuration, derived OHLCV, and backtest evidence

## 1. Purpose

Define one authoritative instrument registry and one shared market-data acquisition pipeline for Weltrade SyntX instruments. Instrument-specific behaviour belongs in versioned configuration, not duplicated data engines or independent trading systems.

This specification also establishes the minimum evidence contract for raw broker observations and every derived dataset consumed by OBI.

This is a specification, not a claim that all listed instruments, symbols, timeframes, or data endpoints have been verified on a live Weltrade account.

## 2. Normative language

The terms **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** are normative requirements.

A missing or unverified value MUST be represented explicitly as `unknown`, `unverified`, or null according to the field definition. It MUST NOT be filled with an inferred broker symbol, contract specification, or market behaviour.

## 3. Architectural principles

1. **One acquisition engine:** all supported instruments use the same transport, validation, persistence, and lineage pipeline.
2. **Configuration-driven interpretation:** family-specific behaviour is represented as versioned metadata/configuration. It does not fork acquisition or create separate strategy engines.
3. **Raw observations are evidence:** source observations MUST be retained before cleaning, deduplication, resampling, indicator calculation, or other transformation.
4. **Derived data is reproducible:** every derived candle or feature set MUST identify its inputs and transformation version.
5. **Fail closed:** missing, stale, malformed, ambiguous, or unverified data MUST NOT be silently treated as valid market data.
6. **Identity is explicit:** a display label is not a broker symbol. A family name is not proof of account availability.
7. **No fabricated production data:** generated or simulated observations MUST never enter the production analysis, signal archive, or backtest evidence path.

## 4. Instrument-family catalogue

The following family names are the initial catalogue supplied for investigation:

| Family ID | Display family | Initial behaviour classification |
|---|---|---|
| `FX_VOL` | FX Vol | Unknown — verify exact product definition |
| `SFX_VOL` | SFX Vol | Unknown — verify exact product definition |
| `PAIN` | PainX | Direction/spike behaviour must be verified from authoritative product documentation and observations |
| `GAIN` | GainX | Direction/spike behaviour must be verified from authoritative product documentation and observations |
| `FLIP` | FlipX | Unknown — verify |
| `SWITCH` | SwitchX | Unknown — verify |
| `BREAK` | BreakX | Unknown — verify |
| `TREND` | TrendX | Unknown — verify |
| `PLUS` | PlusX 1 | Unknown — verify |
| `FIBO` | FiboX | Unknown — verify |
| `QUAD` | QuadX | Unknown — verify |
| `MAX_PAIN` | MAX PainX | Unknown — verify |
| `MAX_GAIN` | MAX GainX | Unknown — verify |

This catalogue is not a broker symbol list. No exact symbol string, trading schedule, tick frequency, spike probability, contract size, or timeframe support is implied by a family entry.

## 5. Registry data model

The registry MUST use stable internal IDs and retain broker identity separately.

### 5.1 Instrument record

| Field | Type | Requirement |
|---|---|---|
| `instrument_id` | string | Required, stable OBI identifier; never derived solely from display name |
| `family_id` | enum/string | Required; references §4 or a reviewed extension |
| `display_name` | string | Required human-readable label |
| `broker` | string | Required, e.g. Weltrade only when verified |
| `broker_symbol` | string/null | Exact symbol returned by the connected broker; null until observed |
| `broker_display_name` | string/null | Broker-provided description, retained verbatim |
| `account_environment` | enum | `demo`, `live`, `contest`, `unknown` |
| `account_fingerprint` | string/null | Stable HMAC-SHA256 pseudonymous fingerprint derived from broker/server/login/trade mode; MUST NOT contain password, token, or full credentials. The HMAC key remains a host secret and is never persisted in the registry |
| `platform` | enum/string | e.g. MT5; `unknown` until verified |
| `server_name` | string/null | Exact broker server name, if available |
| `availability_status` | enum | `unverified`, `visible_in_market_watch`, `selectable`, `data_verified`, `disabled`, `unavailable` |
| `source_type` | enum | `broker_ticks`, `broker_bars`, `third_party`, `simulated_test_only`, `unknown` |
| `supported_timeframes` | array | Only timeframes empirically verified for this exact broker symbol/account |
| `timeframe_status` | map | Per-timeframe status and evidence; absence means unverified |
| `contract_metadata` | object | Broker-reported contract/tick properties; unknown fields remain null |
| `session_metadata` | object | Broker schedule/timezone/maintenance details; not inferred from forex sessions |
| `behaviour_profile_id` | string/null | References a separately versioned family profile |
| `verification_status` | enum | `unverified`, `partially_verified`, `verified`, `rejected`, `stale` |
| `evidence_refs` | array | Immutable IDs/paths for screenshots, API responses, logs, and validation reports |
| `first_observed_at` | timestamp/null | UTC timestamp |
| `last_verified_at` | timestamp/null | UTC timestamp |
| `registry_version` | string | Version of the record/schema |
| `notes` | string | Human review notes; not authoritative structured data |

### 5.2 Evidence required to advance verification

- `unverified → visible_in_market_watch`: exact broker symbol and broker description captured from the target account's Market Watch or equivalent symbol catalogue.
- Account identity remains observed-but-unpinned until an operator reviews the broker, exact server, environment, and masked login suffix, then configures the expected HMAC account fingerprint. Market-data requests are blocked until this pin matches on startup and reconnect.
- `visible_in_market_watch → selectable`: broker API successfully selects the exact symbol and returns symbol metadata.
- `selectable → data_verified`: non-empty broker data returned for a stated timeframe, timestamps and OHLCV pass validation, and raw source evidence is persisted.
- `verified`: all required evidence for the intended use case is present, reviewed, and recent enough for the declared verification policy.

A symbol verified in demo MUST NOT automatically be treated as verified in live. Account environment and broker server are part of instrument identity. The HMAC fingerprint key must remain stable across restarts and be backed up separately from the SQLite evidence store; losing it invalidates existing fingerprints and requires explicit re-pinning.

## 6. Family behaviour profiles

Family profiles are configuration, not executable strategy code. Each profile MUST be versioned and contain:

- `profile_id`, `family_id`, `version`, `status`, and evidence references.
- Authoritative product description and source provenance.
- Observed/confirmed price behaviour, if supported by evidence.
- Known limitations and explicit unknowns.
- Tick/candle characteristics relevant to data interpretation.
- Session/availability assumptions, including maintenance windows if documented.
- Any event/spike statistics only when measured from adequate raw observations with methodology and sample size recorded.
- Effective date and reviewer.

Profiles MUST distinguish **documented specification**, **broker-observed fact**, **statistical estimate**, and **untested hypothesis**. A family label or marketing description alone is not sufficient evidence for a quantitative model.

## 7. Shared acquisition pipeline

The logical pipeline is:

`Broker/MT5 → Raw Observation Store → Validation & Quarantine → Canonical Normalization → Derived Bars/Features → OBI Consumers`

### 7.1 Source adapters

Adapters MAY retrieve ticks or broker-native candles. They MUST return a source envelope containing:

- exact broker, server, account environment, platform, broker symbol;
- requested and returned timeframe/range/count;
- retrieval time in UTC and source timestamps;
- source endpoint/API method and adapter version;
- broker response metadata and error state;
- raw payload or a lossless persisted representation, plus cryptographic content hash.

Credentials MUST be supplied through secrets/environment configuration and MUST NOT be written to logs, registry records, or raw payload metadata.

### 7.2 Raw observation store

Raw observations MUST be append-only from the application's perspective. Corrections or re-fetches create new observation records; they do not overwrite prior evidence.

Each raw record MUST include:
- unique observation ID;
- source identity and exact instrument identity;
- source timestamp as received, normalized UTC timestamp, and timestamp precision/timezone interpretation;
- observation type (`tick` or `broker_bar`);
- original source fields without indicator columns or lossy normalization;
- retrieval/batch ID and ingestion timestamp;
- raw payload hash and schema version.

If storage is not yet available, the acquisition path MUST report that evidence persistence is unavailable and MUST NOT claim the observation is archived. A transient in-memory dataframe is not an immutable raw store.

### 7.3 Validation and quarantine

Validation MUST be explicit and produce a machine-readable report. Invalid observations MUST be quarantined or recorded as rejected with reason codes. They MUST NOT disappear silently.

Minimum checks:
- required fields and schema;
- timestamp parseability, timezone handling, and plausible range;
- numeric conversion, finite values, and valid price/volume domains;
- OHLC invariants: `High >= max(Open, Close)`, `Low <= min(Open, Close)`, `High >= Low`;
- duplicate timestamps and duplicate source IDs;
- ordering and gaps;
- timeframe alignment where applicable;
- stale/future observations relative to retrieval time;
- incomplete/current candle status;
- symbol identity matches the request and broker response.

### 7.4 Duplicate policy

Duplicate observations MUST be retained in raw storage. The canonicalization stage MUST classify duplicates and record the chosen resolution policy.

- Exact duplicate payloads MAY be collapsed in a derived view, with all source observation IDs retained.
- Conflicting observations with the same source identity/timestamp MUST be flagged as a conflict. The system MUST NOT silently choose first/last.
- A conflict-resolution policy, if later approved, MUST be deterministic, versioned, and traceable to all candidate records.

### 7.5 Missing/invalid values

Rows with invalid fields MUST be quarantined with field-level reasons. A derived dataset MAY exclude quarantined rows, but MUST include counts, IDs/ranges, and rejection reasons in its quality report.

No pipeline stage may convert an invalid or missing observation into a plausible price, fill gaps with random values, or silently forward-fill OHLC prices. Any explicitly approved gap treatment must be represented as a separate derived transformation.

### 7.6 Broker bars versus tick-derived bars

Broker-native candles and tick-derived candles are distinct source/derivation classes and MUST NOT be conflated.

- Broker-native bars preserve the broker's OHLCV as received.
- Tick-derived bars are computed from persisted raw ticks using a versioned aggregation algorithm, timezone/boundary rules, and volume definition.
- A resampled timeframe (e.g. 4h from 1h) is derived data and MUST retain source bar IDs, aggregation window, timezone, and algorithm version.
- Tick volume, real volume, and trade count MUST remain distinct fields. A generic `Volume` label is insufficient unless its meaning is declared in metadata.

The current bridge uses MT5 `tick_volume` in the `Volume` output field. This MUST be explicitly labelled as tick volume in the canonical model; it MUST NOT be represented as exchange-traded volume.

### 7.7 Current/incomplete bars

MT5 position-based bar retrieval can include the currently forming bar. The adapter MUST label each bar as `forming` or `closed` using broker time/bar-boundary evidence. Backtests and closed-candle signal evaluation MUST use closed bars only unless a strategy explicitly declares intrabar processing and records the tick-level evidence.

## 8. Canonical data and lineage

A canonical candle MUST include:
- instrument ID and exact broker symbol;
- timeframe and UTC open/close boundaries;
- OHLC values;
- volume value and volume type;
- source class (`broker_bar` or `tick_derived`);
- completion state (`forming` or `closed`);
- source observation IDs or source candle IDs;
- validation report ID/status;
- transformation/aggregation version;
- ingestion and derivation timestamps.

Indicators (EMA, ADX, ATR, etc.) MUST be calculated only after canonical validation. Indicator output MUST be treated as derived data and identify the input candle series/version and indicator implementation/configuration version.

A derived dataset MUST be reproducible from retained source observations and declared transformation versions. If it cannot be reproduced, its lineage status MUST be marked incomplete.

## 9. Acquisition/API contract requirements

The current `mt5_bridge.py` endpoint is a prototype and requires hardening before it is treated as a production evidence source:

1. Require authentication or a private, authenticated network boundary. Binding to `0.0.0.0` without an access-control layer is not acceptable for an exposed service.
2. Validate `symbol`, `timeframe`, and `count`; enforce a maximum count and reject unsupported timeframes rather than silently defaulting.
3. Return exact broker symbol, broker description, server/account environment metadata, timeframe, volume type, and source timestamp semantics.
4. Return explicit errors and status metadata; do not return a success-shaped response for partial or failed retrieval.
5. Make forming/closed candle status explicit.
6. Preserve original MT5 rate fields and raw response evidence before normalization.
7. Record MT5 terminal connection state and symbol metadata; do not treat `symbol_select` success alone as data verification.
8. Use broker-provided symbol names from discovery/verification. Do not rely on hardcoded display-name aliases as proof that an account uses those names.
9. Do not expose credentials in the HTTP interface, logs, URLs, or responses.
10. Add tests for invalid inputs, disconnected terminal, unknown symbol, empty history, partial history, duplicate timestamps, forming bars, and metadata mismatch.

## 10. Initial code audit — observed gaps

The following are observations from the current PR #10 branch, not claims about every runtime deployment:

| Area | Current observation | Required direction |
|---|---|---|
| Synthetic symbol identity | `DataAgent` and `mt5_bridge.py` use five hardcoded internal aliases; bridge maps them to display labels | Replace assumptions with registry-backed exact broker symbol mappings after account verification |
| Family coverage | Code currently lists five synthetic aliases; family catalogue has thirteen family names | Do not add symbols until exact account symbols are observed |
| Raw evidence | Bridge returns normalized JSON rows; DataAgent transforms them directly | Persist original MT5 rates/payload before transformations |
| Duplicate timestamps | DataAgent keeps last duplicate | Retain raw records; report exact/conflicting duplicates; never silently choose |
| Invalid timestamps/numbers | DataAgent drops invalid rows | Quarantine with reason codes and counts |
| Invalid OHLC | DataAgent filters invalid bars | Quarantine and report rejected observations |
| Indicators | Indicators are added after filtering; `dropna` removes warm-up rows | Keep canonical bars separate from feature rows; record indicator warm-up exclusions |
| Volume semantics | MT5 `tick_volume` is exposed as generic `Volume` | Preserve `tick_volume` semantics explicitly |
| Bar completion | Bridge requests bars from position zero and does not label the current forming bar | Mark forming/closed; closed-bar consumers exclude forming bars by default |
| Identity metadata | Response contains requested internal symbol/timeframe, not full account/server/symbol metadata | Include broker identity and verification metadata |
| API security | Flask app binds to all interfaces and has no visible authentication | Restrict network exposure and authenticate before remote use |
| Request validation | `count` is unbounded; unsupported timeframe falls back to M15 | Strict input validation and bounded requests |
| Availability | SessionAgent has a 24/7 rule for the five hardcoded synthetic aliases | Treat market schedule and data availability as separate concepts |

These findings are not all PR #10 blockers. They define the next data-foundation work and MUST be resolved or explicitly accepted before the bridge is used as a trusted historical-data source.

## 11. Verification states and promotion gates

| Gate | Evidence required | Permitted use |
|---|---|---|
| G0 — Catalogued | Family name recorded; all account-specific fields unknown | Documentation only |
| G1 — Account observed | Exact broker symbol, broker description, account environment, server, platform captured | Instrument discovery/UI display |
| G2 — Selectable | MT5 symbol metadata and successful selection captured | Connectivity check only |
| G3 — Data verified | Raw data persisted; timeframe, schema, timestamps, OHLC and volume semantics validated | Data-quality experiments |
| G4 — History validated | Adequate history, gap/duplicate analysis, closed-bar semantics, repeatable retrieval and lineage verified | Research/backtesting with stated limitations |
| G5 — Analysis eligible | Family profile reviewed; feature behaviour and assumptions validated against source evidence | OBI analysis eligibility; not automatic execution permission |

No stage implies permission to place trades. Execution eligibility remains governed by OBI's separate risk and execution controls.

## 12. Pilot selection policy

Do not select a pilot by marketing prominence or assumed trading attractiveness.

After the registry is populated from the actual MT5 account, rank candidates using:
1. exact symbol discoverability and repeatable selection;
2. reliable broker data retrieval;
3. raw tick or bar availability and retention depth;
4. timeframe coverage and timestamp quality;
5. reproducible history retrieval;
6. clear volume and bar-completion semantics;
7. manageable family-specific assumptions.

Select one pilot only after these facts are recorded. If no candidate meets G3, the correct result is **no pilot yet**.

## 13. Implementation sequence

1. **Registry schema and validation:** introduce versioned registry records and schema tests; no new family logic.
2. **Bridge contract hardening:** symbol discovery/metadata, request validation, authentication/network boundary, forming-bar semantics.
3. **Raw evidence persistence:** append-only observation batches, hashes, and retrieval manifests.
4. **Quarantine and quality reports:** duplicates, invalid rows, OHLC failures, gaps, stale data, and volume semantics.
5. **Canonical bars and lineage:** separate raw observations, canonical bars, resampled bars, and indicators.
6. **Account verification:** populate only exact symbols and timeframes observed in the user's broker environment.
7. **Pilot decision:** choose one instrument after G3/G4 evidence.

Each implementation step requires tests and a reviewable CI result. No synthetic strategy logic, new signal rules, or broad family rollout is in scope until the data foundation is demonstrated.

## 14. Acceptance criteria for v0.1

- [ ] Registry schema distinguishes family, exact broker symbol, account environment, platform, server, source, and timeframe verification.
- [ ] Unknown values remain explicitly unknown; no broker symbol is invented.
- [ ] All thirteen supplied family names are represented without claiming account availability.
- [ ] Shared acquisition engine and family-profile separation are explicit.
- [ ] Raw observation preservation and lineage are mandatory.
- [ ] Duplicate and invalid-row policies prohibit silent data loss.
- [ ] Broker bars, tick-derived bars, resampled bars, and indicator features are distinct.
- [ ] Volume semantics and forming-bar state are explicit.
- [ ] Current bridge gaps and security concerns are documented.
- [ ] No pilot is selected without account-level evidence.

## 15. Change control

This document is versioned independently from implementation. Changes to schema fields, validation rules, duplicate policy, volume semantics, or verification gates require a version increment and corresponding tests. Any implementation that weakens raw evidence preservation or allows fabricated observations into production MUST be rejected.

**End of specification.**
