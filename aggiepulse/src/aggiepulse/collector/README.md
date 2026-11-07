TripSpark collector
==================

From the `aggiepulse` package directory:

```sh
uv run python -m aggiepulse.collector.poll_tripspark --interval 30 --base-interval 3600
```

For a laptop backup that prevents idle sleep on macOS and restarts after exits:

```sh
nohup ./scripts/collector/run_backup_collector.sh --interval 30 --base-interval 3600 >/dev/null 2>&1 &
```

Stop the runner with SIGTERM to its PID; it forwards SIGTERM to the collector.
`UV_BIN` can specify an absolute path to uv. Logs go to `data_root()/logs/`.

`AGGIEPULSE_DATA_ROOT` overrides the shared helper's default storage root. Set it
explicitly for a UC Volume or another local directory. Vehicle files land at
`landing/tripspark_vehicles/date=YYYY-MM-DD/vehicles_<UTC-hour>_<hostname>.jsonl.gz`.
Each poll is a separate, closed gzip member followed by flush/fsync. Standard
Python gzip and Spark can read the concatenated members. Base fetch envelopes
are separate files under `landing/tripspark_base/date=YYYY-MM-DD/`. Failed
fetches retain their status and error with null payload; status 0 means there
was no HTTP response for that fetch. The heartbeat at
`data_root()/collector_heartbeat.json` is atomically replaced after each poll
and on shutdown, and contains counters scoped to the current process run.

`--once` performs one poll. `--max-polls N` bounds a run. Startup polls immediately;
subsequent polls align to epoch boundaries, skipping missed slots. Base data
refreshes at startup and hourly boundaries, retaining prior route keys if a
refresh fails. Each fetch retries three times with new cookies/token after an
error, using interruptible exponential backoff capped at five minutes. After
retry exhaustion the failed fetch is recorded, and collection continues.
SIGINT/SIGTERM interrupts waits and finishes any in-flight bounded HTTP request.

The server requires the AJAX request header as well as its verification token.
TLS verification uses the OS trust store through `truststore`, since certifi
alone could not verify the live server on the development laptop. The client
only accesses `/RouteMap` and its `GetBaseData`/`GetVehicles` endpoints.

`flatten_vehicles(payload, ingest_ts, base_data=None)` returns typed dictionaries
with aware UTC datetime values. The feed is a list of route groups containing
`vehiclesByDirections`, which contain `vehicles`; GPS is nested in `location`.
`vehicle_id` is the vendor GUID, and `vehicle_name` is the fleet label. Route
short names require base data unless supplied in the vehicle payload. Speed
units are undocumented and retained without conversion. Load factors can exceed
1; missing/zero capacity gives null load factor. Missing measurements stay null.
Naive GPS timestamps use America/Chicago. During fall-back the latest fold not
after ingestion is selected; nonexistent spring times and ambiguous times with
both folds in the future raise ValueError.

Fixtures contain three actual route groups captured on 2026-11-07 and route
metadata. The saved bootstrap script uses a synthetic verification token.
