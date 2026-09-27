# OBI Direction Replay

Pass 13 Stage 1 provides a deterministic harness for reproducing the production direction path without changing production agent logic.

## Direction path

`HTFAgent -> MTFAgent -> BiasAgent -> TriggerAgent`

## Replay case contract

Each case requires:

- `case_id`
- `symbol`
- `opened`
- `agent_inputs.session`
- `agent_inputs.regime`
- `agent_inputs.ltf`
- `agent_inputs.zone`
- `agent_inputs.htf` (reserved for captured provenance)
- `agent_inputs.mtf` (reserved for captured provenance)

For raw-market-data replay, `market_data` contains serialized timeframe rows. The current harness converts those rows into pandas DataFrames before invoking HTF/MTF.

## Stage 1 boundary

The test fixtures are controlled engineering fixtures. They verify the replay mechanism and provenance path only. They are **not** production historical evidence and must not be used to infer trading performance.

Production replay begins only after real historical OHLC or captured runtime inputs are supplied.
