# AggiePulse platform operations

All CLI operations use profile `transit`. Run commands from `/Users/jboi/Projects/Transit/aggiepulse`.

## Egress and scheduling decision

The one-off **serverless** probe on 2026-11-07 reached Open-Meteo archive, Open-Meteo forecast, and NWS with HTTP 200. TripSpark RouteMap, TAMU GTFS (HEAD), TAMU LiveWhale events, and NTD failed DNS resolution (`[Errno -3] Temporary failure in name resolution`). See `egress_results.json` and `egress_check.py` for exact endpoints, methods, and the run URL. The probe made no GetVehicles calls and never accessed `/Schedule`.

Consequently `resources/jobs_ingest.yml` deploys only `ingest_weather_hourly`: one serverless task at minute 10 hourly in America/Chicago, using the bundle wheel dependency. It fetches only the Open-Meteo forecast and NWS observations; the historical Open-Meteo archive is not repeatedly downloaded hourly. `AGGIEPULSE_DATA_ROOT` is set to a local temporary directory while ingest writes gzip; completed files are then copied sequentially to `/Volumes/transit/raw/landing`. This avoids gzip seek/append operations on Volume FUSE. The destination comes from `${var.landing_root}` and defaults to `/Volumes/transit/raw`.

Full `python -m aggiepulse.ingest all` remains a laptop operation because four required serverless domains fail DNS. No daily all-sources serverless job is deployed. A future Databricks network-policy change must be followed by a fresh serverless probe before enabling full batch or collector jobs. `python -m aggiepulse.platform.batch all --data-root /Volumes/transit/raw` already provides the local-staging adapter for a future daily task without changing the ingest modules.

`resources/jobs_collector.yml` documents why no collector resource is deployed. The laptop collector remains unchanged at its existing 30-second cadence. There is no Databricks TripSpark poller, no extra GetVehicles traffic, and no continuously running serverless job. The laptop uses five-minute vehicle files to reduce immutable-file ingestion delay; the default collector rotation remains hourly.

## Start the sync loop

```sh
cd /Users/jboi/Projects/Transit/aggiepulse
nohup ./scripts/collector/run_backup_collector.sh --interval 30 --base-interval 3600 --rotate-minutes 5 >/dev/null 2>&1 &
nohup bash scripts/platform/run_sync_loop.sh --rotate-minutes 5 >>data/logs/platform_sync.log 2>&1 &
nohup bash scripts/platform/run_publish_live_loop.sh --interval 30 >>data/logs/publish_live.log 2>&1 &
```

The sync runner uploads immediately, then every 300 seconds. The live publisher runs every 30 seconds. Stop each runner with SIGTERM; the collector and publisher runners forward it to their child. Logs remain under `data/logs/`. Run only one instance of each loop.

For a single pass:

```sh
PATH=/opt/homebrew/bin:$PATH uv run python scripts/platform/sync_to_volume.py --rotate-minutes 5
```

The sync preserves all paths relative to `data/landing/` under `/Volumes/transit/raw/landing/`. It skips files modified within 120 seconds, TripSpark vehicle files whose filename timestamp plus rotation length has not elapsed, unknown vehicle filenames, and hidden/symlink files. It snapshots stable inputs locally, verifies size/mtime did not change during snapshotting, creates Volume parent directories, copies without `--overwrite`, then downloads and verifies the remote SHA-256 before recording success. If an object already exists, the CLI skips uploading it and the same verification still runs. A mismatch stops the pass; it never repairs a conflict by overwriting the immutable object.

Hourly names always use a 60-minute closure rule, including previously uploaded
hourly files. Minute names use `--rotate-minutes` (default 5); match the collector.
The 120-second mtime guard still applies to every file. Core Auto Loader accepts
both names with `*.json*`; source-host extraction supports both timestamp lengths.

The manifest is `data/platform_sync_manifest.json` (path, size, sha256 for each completed upload), with an advisory lock to prevent overlapping local runners. Keep one manifest per destination Volume when using custom `--volume-root` values. A crash before manifest persistence is safe: the next pass verifies the existing object. Changed local files that were already uploaded cause an explicit immutable-file conflict. The loop retries failed passes next cycle.


## Live vehicle sidecar and app selection

`run_publish_live_loop.sh` supplies `databricks-sdk` through `uv run --with`, using
profile `transit`. It reads complete gzip records from local landing files,
selects the latest HTTP-200 successful vehicle poll (including empty polls), and
uses `aggiepulse.collector.parse` with the latest successful base metadata for
route short names and direction colors. Partial final gzip members and failed
polls retain the preceding success. It never requests TripSpark data.

The sidecar creates managed Volume `transit.raw.live` if absent and overwrites
only `/Volumes/transit/raw/live/latest_vehicles.json` using the
[SDK Files API](https://databricks-sdk-py.readthedocs.io/en/stable/workspace/files/files.html).
The JSON contains `snapshot_ts`, `poll_ingest_ts`, `poll_id`, and `vehicles`.
Errors log one compact warning and retry on the next cycle. For a single pass:

```sh
uv run --with databricks-sdk python scripts/platform/publish_live.py --once
```

The Databricks app checks this file first, requiring both publication and poll
age to be at most 180 seconds. Its source is `hot-path`. On a stale/missing file
it uses fresh Silver rows (`databricks`, GPS within ten minutes), then the
packaged fallback (`local-snapshot`). A fresh empty hot poll remains empty.
`/api/meta` includes source, fallback reason, poll/publish timestamps, latest GPS,
and `age_s` (poll age for hot data; latest GPS age otherwise). Vehicle rows also
carry their source. The app bundle declares READ_VOLUME on `transit.raw.live`;
its service principal also needs USE CATALOG/SCHEMA (already granted here).

Redeploy after backend changes:

```sh
databricks bundle deploy -p transit
databricks apps deploy aggiepulse -p transit --source-code-path /Workspace/Users/<redacted-email>/.bundle/aggiepulse/transit/files/app
```

The warm path still depends on the mtime guard, sync cadence, the next 15-minute
core/ops trigger and pipeline execution time. The hot path is independent of
pipeline scheduling. Historical verification below describes the earlier hourly
configuration; current process and latency measurements are in the rollout report.

## Laptop daily batch

Run `bash scripts/platform/run_daily_ingest.sh` at approximately 06:00 America/Chicago using the coordinator's laptop scheduler. For a cron daemon configured in America/Chicago:

```cron
0 6 * * * /Users/jboi/Projects/Transit/aggiepulse/scripts/platform/run_daily_ingest.sh >> /Users/jboi/Projects/Transit/aggiepulse/data/platform_daily_ingest.log 2>&1
```

The script invokes exactly `uv run python -m aggiepulse.ingest all`. The separate sync loop uploads its completed output after the stability guard. No cron entry or laptop scheduler was installed automatically.

## Validate and inspect

```sh
uv run pytest tests/platform -q
uv run ruff check src/aggiepulse/platform scripts/platform tests/platform
uv run pytest -q
databricks bundle validate -p transit
databricks bundle deploy -p transit
PATH=/opt/homebrew/bin:$PATH uv run python scripts/platform/volume_inventory.py
```

If another agent holds the deployment lock, wait and retry without `--force-lock`.

## Cost controls

The hourly weather job uses STANDARD performance, one task, one concurrent run, zero requested task retries, and a 900-second timeout. Each execution makes one Open-Meteo forecast request and one NWS observations request, with the ingest HTTP client's bounded retries on transient failures. The initial verification run uses PERFORMANCE_OPTIMIZED to avoid waiting for cost-optimized startup. Databricks may perform internal automatic retries independently of the task retry setting. No quota rejection was observed. Actual DBU cost has not been measured; the schedule creates 24 short job starts per day. Neither the full batch nor continuous collector consumes serverless compute in this configuration.

## Implementation verification (2026-11-07)

- Bundle deployed successfully with `databricks bundle deploy -p transit`; one overlapping deployment lock was retried without forcing it.
- Weather job ID: **873387104498194** — https://dbc-595c228e-76f6.cloud.databricks.com/jobs/873387104498194?w=7474654155800707
- Successful verification run: **723607039332276** — https://dbc-595c228e-76f6.cloud.databricks.com/?o=7474654155800707#job/873387104498194/run/723607039332276
- The successful run finished in 41.9 seconds and published `weather_nws/date=2026-11-07/20261107T200311.290902Z_raw.json.gz` and `weather_openmeteo/date=2026-11-07/20261107T200311.146648Z_forecast_raw.json.gz`.
- An earlier verification attempt published two files but was marked failed because `SystemExit(0)` is considered an error by Databricks' IPython script runner. Its automatic retry was cancelled; the launcher now returns normally on success, with regression tests.
- Real local backfill: **31 files** recorded in the checksum manifest. Final idempotence check: `uploaded=0, unchanged=31, deferred=1`. The deferred file is the active 20:00 UTC vehicle file; the completed 19:00 UTC file was uploaded and remotely verified.
- Validation: **14 platform tests passed**, **96 total tests passed**, scoped Ruff clean, bundle validation clean. The overall suite has one pre-existing Starlette/httpx deprecation warning.
- No local loop, local collector replacement, cron entry, or background helper was left running. The hourly Databricks weather schedule is enabled.

Volume inventory at verification time, under `/Volumes/transit/raw/landing`:

| Source | Files | UTC landing partition date range |
| --- | ---: | --- |
| events | 19 | 2026-11-07 through 2026-11-07 |
| gtfs | 2 (ZIP + sidecar) | 2026-11-07 through 2026-11-07 |
| ntd | 1 | 2026-11-07 through 2026-11-07 |
| tripspark_base | 3 | 2026-11-07 through 2026-11-07 |
| tripspark_vehicles | 1 | 2026-11-07 through 2026-11-07 |
| weather_nws | 3 | 2026-11-07 through 2026-11-07 |
| weather_openmeteo | 6 | 2026-11-07 through 2026-11-07 |

These seven source directories contain **35 files / 3,789,007 bytes**. Date ranges describe landing partitions, not the historical timestamps inside payloads. Other agents' derived/extracted directories are outside this inventory.

Files created by this workstream:

- `resources/jobs_ingest.yml`
- `resources/jobs_collector.yml` (egress decision only; no collector resource)
- `src/aggiepulse/platform/__init__.py`
- `src/aggiepulse/platform/sync.py`
- `src/aggiepulse/platform/batch.py`
- `scripts/platform/sync_to_volume.py`
- `scripts/platform/run_sync_loop.sh`
- `scripts/platform/run_daily_ingest.sh`
- `scripts/platform/run_batch.py`
- `scripts/platform/egress_check.py`
- `scripts/platform/egress_results.json`
- `scripts/platform/volume_inventory.py`
- `scripts/platform/README.md`
- `tests/platform/test_sync.py`
- `tests/platform/test_batch.py`

No collector, ingest, shared bundle, dependency, or other workstream files were edited. No commit was created.
