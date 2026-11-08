# MT5 Bridge Deployment Notes

## What the bridge is

The bridge is a read-only HTTP adapter to a running MetaTrader 5 terminal. It is **not a broker** and does not generate market data. The MT5 terminal must already be logged into the target broker account (demo or live), or the bridge must be given the complete login configuration through environment variables. Connection lifecycle, broker/server identity checks, symbol discovery, exact symbol selection, and rate retrieval are implemented in core/mt5_connector.py.

OBI receives broker rates only after the terminal is connected, broker/server identity checks pass, the exact broker symbol is mapped and selectable, and the original MT5 rates have been persisted to the raw-observation database. The connector exposes no order submission, position modification, or trade-management methods.

## Install the bridge runtime

Run this on the Windows host where the MT5 terminal is installed. The MetaTrader5 Python package is platform-specific and is intentionally not installed in the Linux GitHub Actions test runner.

```powershell
python -m pip install Flask MetaTrader5 waitress
```

## Required environment

Set these on the Windows machine or VPS that runs the MT5 terminal:

- `MT5_BROKER_NAME`: broker name, e.g. `Weltrade`.
- `MT5_ACCOUNT_ENVIRONMENT`: `demo`, `live`, or `contest`. The connector verifies this against the actual MT5 `account_info().trade_mode` value using the MetaTrader5 package constants; a mismatch or unavailable trade mode fails closed.
- `MT5_SYMBOL_MAP_JSON`: JSON mapping from OBI's internal instrument IDs to exact symbols visible in this account.
- `MT5_RAW_DB_PATH`: durable SQLite file path shared by raw observations, canonical candles, and the append-only account/instrument registry. Default: `data/raw_observations.sqlite3`.
- `MT5_BRIDGE_HOST`: defaults to `127.0.0.1`.
- `PORT`: defaults to `5000`.

Example only — replace the broker symbol with the exact string shown in your own MT5 Market Watch:

```powershell
$env:MT5_BROKER_NAME = "Weltrade"
$env:MT5_ACCOUNT_ENVIRONMENT = "demo"
$env:MT5_SYMBOL_MAP_JSON = '{"SFX_VOL_75":"EXACT SYMBOL FROM MARKET WATCH"}'
$env:MT5_RAW_DB_PATH = "D:\OBI\data\raw_observations.sqlite3"
$env:MT5_BRIDGE_HOST = "127.0.0.1"
python mt5_bridge.py
```

Do not copy the example placeholder as a real symbol. Confirm it in the connected account first.

If the terminal is already logged in, the bridge can use that session. Alternatively set all three variables `MT5_LOGIN`, `MT5_PASSWORD`, and `MT5_SERVER`; never commit them to Git or place them in source code.

## Remote access

The default loopback binding is intended for OBI and MT5 to run on the same machine. If remote access is required:

1. Set `MT5_BRIDGE_HOST=0.0.0.0` only behind a private network or authenticated tunnel.
2. Set a strong `MT5_BRIDGE_TOKEN` secret on both the bridge host and OBI caller.
3. Set OBI's `MT5_BRIDGE_URL` to the private reachable endpoint and `MT5_BRIDGE_TOKEN` to the same secret.
4. Restrict network ingress to the OBI caller. Do not expose the bridge publicly without a reviewed network security design.

A GitHub-hosted Actions runner cannot reach a bridge at your computer's `localhost`. Either run OBI beside the bridge or provide a private, authenticated route from the runner. Do not solve this by exposing an unauthenticated public endpoint.

## Data persistence and eligibility

The bridge writes original MT5 rate fields to SQLite before returning data. If persistence fails, the request fails and OBI withholds the data. SQLite update/delete triggers prevent application-level mutation; the database file itself still requires normal filesystem access controls and backups.

The response labels tick volume explicitly. A request from MT5 position zero marks the newest returned bar as `forming`; paginated historical requests (`start_pos` greater than zero) mark every returned bar as `closed`. DataAgent excludes forming bars from closed-candle analysis. Duplicate timestamps are rejected from the analysis view; the raw observations remain stored for review.

A successful HTTP response does not by itself promote an instrument to verified. Exact symbol, broker/server, account environment, timeframe, data quality, and persisted evidence must still be reviewed.


## Connector capabilities and API

The reusable connector has explicit lifecycle states: DISCONNECTED, INITIALIZING, CONNECTED, AUTHENTICATED, and ERROR. A session is trusted only after terminal connectivity, configured broker/server identity checks, and actual MT5 account trade-mode verification pass. Access to the process-wide MT5 API is serialized across HTTP worker threads so concurrent requests cannot interleave terminal operations. If the session drops, the connector performs one bounded reconnect attempt when a data request arrives; it does not retry indefinitely.

- GET /health reports connection state only.
- GET /symbols returns account-scoped symbol discovery and contract metadata (digits, point, tick size/value, contract size, currencies, volume limits, path, and availability flags). Every call appends the account observation and symbol snapshot to the SQLite registry. Discovery does not classify a symbol as SyntX or mark it analysis-eligible.
- GET /ohlcv requires a prior /symbols discovery for the same account fingerprint and exact broker symbol. A changed contract-metadata fingerprint or ambiguous OBI mapping blocks the request until reviewed and rediscovered.
- GET /ohlcv?symbol=OBI_ID&timeframe=5m|15m|1h|4h&count=1..20000&start_pos=0..1000000 returns mapped broker rates after persistence. Requests up to 20,000 bars are fetched in 2,000-bar pages. The bridge reassembles pages chronologically and checks that the newest bar timestamp did not change during multi-page retrieval. Position zero may include the current bar; forming/closed status is determined from the UTC timeframe boundary, not array position.

Only exact symbols configured in MT5_SYMBOL_MAP_JSON can be requested through the OBI instrument ID. Symbol discovery is evidence for account-specific mapping, not automatic authorization. Timeframes are explicit; unsupported values fail rather than falling back.

## Execution boundary

This release is market-data only. The connector has no order_send, order modification, position closing, or position-management interface, and the Flask service has no execution endpoint. Do not add execution to this service as a convenience extension. Any future execution capability must be a separately reviewed gateway with independent risk limits, authorization, audit records, and explicit operator enablement.

## Live verification status

Automated tests use a fake MT5 adapter. They validate connector lifecycle, authentication configuration, broker/server identity rejection, symbol discovery and selection, timeframe/count validation, empty-history handling, and bounded reconnect behavior. They do not establish that a real Weltrade terminal, account, server, or SyntX instrument has connected successfully. The bridge is served through Waitress rather than Flask's development server. Live acceptance still requires deployment on a Windows host with the actual MT5 terminal and account.


## Durable row-level quarantine

Every MT5 rate row is appended to the raw-observation table before normalization or analysis. The bridge then records validation failures in a separate append-only `quarantine_records` table in the same SQLite transaction. Each quarantine record links to the exact raw `observation_id`, batch, reason code, validator version, and quarantine timestamp. Updates and deletes are blocked by SQLite triggers; this is an application-level guard, not protection against direct database-file tampering.

Current reason codes:
- `INVALID_TIMESTAMP`: missing, non-finite, non-positive, unrepresentable, or non-integer Unix timestamp.
- `INVALID_NUMERIC`: non-finite/non-numeric OHLC or non-integer tick-volume, real-volume, or spread values.
- `INVALID_VOLUME`: negative tick volume, real volume, or spread.
- `INVALID_OHLC`: non-positive prices or candle-boundary violations.
- `DUPLICATE_TIMESTAMP`: timestamp repeated within the retrieved batch; every conflicting row is quarantined.

The API response includes `quality_report` counts and row-level `quarantine_reasons`. DataAgent verifies the raw observation IDs, quarantine IDs, batch count, reason counts, and row annotations agree. It excludes quarantined rows before calculating indicators. Forming bars are not quarantined; they remain raw evidence but are excluded from closed-candle analysis.

Non-finite numeric source values are preserved in the raw JSON payload using an explicit sentinel object, so malformed broker observations remain inspectable without emitting invalid JSON numbers.


## Canonical candle persistence and lineage

The bridge writes two distinct durable layers to the same SQLite database:

1. **Raw observations** retain the exact broker payload, including malformed or duplicate rows.
2. **Canonical candles** are created only for rows that pass the bridge validator. Each canonical candle links to exactly one raw observation for broker-bar source data, preserves broker/server/account/symbol/timeframe identity, UTC open/close boundaries, OHLC, tick-volume semantics, forming/closed state, and transformation version.

A validation report is persisted for every fetched batch, including batches where every row is quarantined and no canonical candles are eligible. The report is append-only and hashed. Canonical candles reference its report ID. The report and canonical rows are committed together; if canonical persistence fails, the bridge withholds the HTTP response while retaining the already-committed raw evidence for investigation.

The API response includes:

- `quality_report.report_id`: immutable validation report identifier.
- `canonical_persistence.persisted`: confirms the canonical write completed.
- `canonical_persistence.candle_ids`: canonical IDs in response order for eligible rows.
- `canonical_persistence.source_lineage`: exact canonical-candle-to-raw-observation pairs.
- `canonical_persistence.validation_report_sha256`: hash of the persisted report.

DataAgent requires the canonical receipt and verifies that the mapping covers every non-quarantined observation exactly once. It also re-runs its independent row checks; if those checks disagree with the bridge's canonical set, the batch is withheld. Accepted analysis DataFrames retain `CanonicalCandleID` and `RawObservationID` columns so downstream OBI consumers can preserve provenance. Forming candles are persisted with their status but excluded from closed-candle analysis.

This is the first direct lineage integration. Derived indicator/feature dependency graphs and resampled-candle lineage remain separate follow-on work; signal-level archive provenance is described below.


## Signal archive lineage manifest

Each scan builds a bounded `obi-data-lineage-v3` manifest from the market-data frames supplied to the analysis pipeline. The archive records, per timeframe, the source batch ID, validation report ID and hash, broker/account/symbol identity, bar count, first/last bar times, a compact row-quality summary, and the last 50 canonical/raw IDs. The batch and report references allow the complete evidence set to be resolved from SQLite without copying hundreds of candle IDs into every signal record.

Manifest status is explicit. These labels describe provenance coverage for the data frames used by the scan, not broker-history completeness, data quality approval, or trading eligibility:
- `complete`: every expected timeframe has canonical IDs and a complete immutable source/report reference.
- `partial`: at least one timeframe is canonical or partially traced, but one or more expected timeframes are missing or incomplete.
- `untracked`: market data exists but no canonical source lineage is available (for example, third-party Yahoo Finance data).
- `missing`: no expected timeframe supplied market data.

Archive entries deep-copy the manifest so later in-memory changes cannot rewrite the recorded provenance. Signal-level source provenance and DataAgent indicator formula lineage are now recorded; structural agent logic and derived/resampled outputs still need their own versioned lineage records.


## Derived indicator feature lineage

DataAgent attaches an `obi-feature-lineage-v1` manifest to every frame after indicator calculation. The current versioned feature set is `data-agent-indicators-v1` and records the exact formula, inputs, parameters, output column availability, calculation library, and installed library version for:

- ADX(14): `ta.trend.adx(High, Low, Close)`
- EMA(20): `ta.trend.ema_indicator(Close)`
- EMA(50): `ta.trend.ema_indicator(Close)`
- ATR(14): `ta.volatility.average_true_range(High, Low, Close)`, stored as `ATRr_14`

Feature lineage status is independent of source lineage status. An indicator is marked available only when its output column exists and its latest value is finite. A frame may have canonical raw-data provenance while its feature manifest is partial if an indicator output or calculation-library version is unavailable. The feature manifest is included in the per-timeframe signal archive lineage. This does not yet version the structural logic inside HTFAgent, MTFAgent, ZoneAgent, LTFAgent, RegimeAgent, BiasAgent, TriggerAgent, or ScoreAgent.


## Account fingerprint and persistent instrument discovery

The connector now requires an actual MT5 account login identifier to be present in account metadata, even when explicit login credentials are not configured. On successful authentication it pins a SHA-256 account fingerprint derived from the broker company, exact server, login identifier, and actual MT5 trade-mode constant. The raw login and password are never returned by the bridge or written to the registry. The fingerprint is an opaque account correlation key, not an authentication secret.

The connector retains the authenticated fingerprint across reconnect attempts. A switch to a different account—even on the same broker server and with the same demo/live mode—fails closed instead of silently re-binding the running bridge. Restarting the bridge is required to intentionally establish a different account identity.

The /symbols endpoint persists an append-only account observation and account-scoped symbol discovery snapshot. Each discovered broker symbol includes exact name and available contract metadata. OBI's internal-to-broker mapping is matched by exact string only. If multiple internal IDs map to the same broker symbol, the registry records a mapping conflict and /ohlcv refuses that mapping.

The /ohlcv endpoint requires the exact symbol to have been discovered under the current account fingerprint. It compares the current symbol contract metadata with the last persisted discovery fingerprint; a mismatch requires rediscovery and review. Raw observations now also persist the account fingerprint (schema version 2); existing SQLite databases are migrated additively, leaving historical rows with a null fingerprint rather than inventing one.

Discovery records remain unverified or partially_verified. Merely seeing or selecting a symbol, or successfully retrieving one batch, does not automatically promote it to verified. A later operator verification step must record timeframe-specific evidence and explicit approval before a symbol can be treated as fully verified.


Account fingerprints are also included in each immutable validation report and raw-observation row, then validated by DataAgent and carried into the archived signal's per-timeframe source lineage. A signal can therefore be traced to the exact broker account identity without exposing the account login number.


## Broker-history completeness gate

Each OHLCV response now includes an immutable history-completeness subreport. The report is scoped to the exact requested window (count and start position); it does not claim that all older broker history exists. It records requested/returned counts, page boundaries, snapshot stability, timestamp ordering/alignment, duplicates, gaps, forming/closed bars, freshness, and a status of complete, incomplete, or unverifiable. A complete status certifies only the requested window, not the broker's entire historical archive.

Unexplained gaps or stale latest data remain unverifiable when no verified broker session calendar is available. They are not silently treated as missing bars or as valid continuous-market history. A verified continuous 24/7 session model can classify those gaps as incomplete. DataAgent accepts only complete history windows; incomplete or unverifiable windows are persisted as evidence but withheld from signal generation. The archived per-timeframe data lineage now uses schema v3 and carries the history report summary alongside the account fingerprint.
