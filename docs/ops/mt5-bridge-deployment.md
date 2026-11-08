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
- `MT5_RAW_DB_PATH`: durable SQLite file path. Default: `data/raw_observations.sqlite3`.
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
- GET /symbols returns terminal-visible symbol names and selection/visibility flags. Discovery does not classify a symbol as SyntX or mark it analysis-eligible.
- GET /ohlcv?symbol=OBI_ID&timeframe=5m|15m|1h|4h&count=1..2000&start_pos=0..1000000 returns mapped broker rates after persistence. Position zero includes the current bar; positive positions page backward through historical bars.

Only exact symbols configured in MT5_SYMBOL_MAP_JSON can be requested through the OBI instrument ID. Symbol discovery is evidence for account-specific mapping, not automatic authorization. Timeframes are explicit; unsupported values fail rather than falling back.

## Execution boundary

This release is market-data only. The connector has no order_send, order modification, position closing, or position-management interface, and the Flask service has no execution endpoint. Do not add execution to this service as a convenience extension. Any future execution capability must be a separately reviewed gateway with independent risk limits, authorization, audit records, and explicit operator enablement.

## Live verification status

Automated tests use a fake MT5 adapter. They validate connector lifecycle, authentication configuration, broker/server identity rejection, symbol discovery and selection, timeframe/count validation, empty-history handling, and bounded reconnect behavior. They do not establish that a real Weltrade terminal, account, server, or SyntX instrument has connected successfully. The bridge is served through Waitress rather than Flask's development server. Live acceptance still requires deployment on a Windows host with the actual MT5 terminal and account.
