# AggiePulse operations through the November 14 game

Work from `/Users/jboi/Projects/Transit/aggiepulse`. All Databricks commands use
`-p transit`. Do not run a second collector. Raw Volume files are immutable.
The deployment verification never stops/restarts the three laptop loops.

## Check health

```sh
databricks bundle run ops_health -p transit
cat data/collector_heartbeat.json
tail -n 30 data/logs/platform_sync.log data/logs/publish_live.log
ps -axo pid,ppid,command | rg 'run_backup_collector|poll_tripspark|run_sync_loop|sync_to_volume|run_publish_live_loop|publish_live.py'
```

Query `SELECT * FROM transit.gold.ops_health ORDER BY checked_ts DESC LIMIT 10`
in the Serverless Starter Warehouse (`3c5b1dbf21774cde`). Fields measure minutes
since the newest successful Bronze poll, Bronze-to-Silver lag, scoring age, and
today's APC pings/stuck minutes/over-capacity pings. Bronze freshness includes
sync/refresh latency; use the local heartbeat/logs to distinguish a collector
outage from delayed uploads/pipelines. Fresh empty vehicle payloads count as
successful collection. Pipeline lag can be nonzero with no active buses.
The health job fails **after saving its row** if no successful Bronze poll exists
or its age exceeds 30 minutes during a route's scheduled first-to-last stop
interval. Service is based on `silver.schedule_expanded`, in UTC; overnight
GTFS service timestamps remain intact. Outside service it records OK and keeps
all ages visible. Failure emails go to `<redacted-email>`.

## Restart laptop processes only when necessary

Read `src/aggiepulse/collector/README.md` and `scripts/platform/README.md`.
Inspect the `ps` command above and identify the runner PID, its children, and
arguments before any stop. Replace `RUNNER_PID` below with the **one** verified
PID; never use broad `pkill` or kill all Python/uv processes. Do one loop at a
time. Use SIGTERM, wait for children to exit, confirm `ps`, then start exactly
one replacement. Do not restart functioning loops during collection.

```sh
kill -TERM RUNNER_PID
# Re-run ps above until that runner AND its child are gone, then start its replacement.
```

Collector runner forwards SIGTERM to its child; it finishes its bounded HTTP
request and flushes gzip records. Verify the heartbeat advances after restart:

```sh
nohup ./scripts/collector/run_backup_collector.sh --interval 30 --base-interval 3600 --rotate-minutes 5 >/dev/null 2>&1 &
```

Sync runner may still have an in-flight `sync_to_volume.py` child after SIGTERM.
**Wait for that upload pass to finish** before restarting; do not kill the
uploader midway or delete its advisory-lock/manifest files. Then:

```sh
nohup bash scripts/platform/run_sync_loop.sh --rotate-minutes 5 >>data/logs/platform_sync.log 2>&1 &
```

Publisher runner forwards SIGTERM and waits for its child; then:

```sh
nohup bash scripts/platform/run_publish_live_loop.sh --interval 30 >>data/logs/publish_live.log 2>&1 &
```

These commands match the current platform README. Collector logs are
`data/logs/collector_backup_$(hostname -s).log`; live publishing does not query
TripSpark. The sync manifest `data/platform_sync_manifest.json` tracks remote
checksums: never overwrite a conflicting landed object. Keep 5-minute rotation
on collector **and** sync; completed files also need the 120-second stability
buffer. Current-hour legacy files retain their 60-minute closure rule.

## TripSpark response/token changes or blocking

Inspect failed envelopes, HTTP status, collector logs and heartbeat first.
The existing client refreshes cookies/verification token after non-JSON/errors,
requires the AJAX header, and retries with bounded backoff. Keep the 30-second
cadence; do not add extra pollers or bypass access restrictions. Only
`/RouteMap/*` is used; `/Schedule/*` is disallowed. Preserve all landed envelopes
and last good snapshots. Update/fixture-test the parser/client before resuming
if the response shape changes. If blocked, use frozen replay for the demo and
ask the coordinator to contact TAMU at `<redacted-email>`; this runbook sends no
email. A serverless DNS failure is not evidence the laptop endpoint is blocked.

## Retrain and roll back

Nightly `ml_train` runs at 03:30 America/Chicago, with data refresh → train →
evaluate → conditional register/promote. Manual trigger:

```sh
databricks bundle run ml_train -p transit
```

Review MLflow `/Shared/aggiepulse`, `metrics/promotion_gate.json`,
`metrics/observed_calibration.json`, `metrics/headline.csv`, and
`registry/registration.json`. New versions are registered even on gate failure;
`@champion` moves only on pass. At h30, >=200 evaluated observed labels spanning
>=2 distinct Chicago weekday dates activates the observed B1-vs-LightGBM MAE
gate. Otherwise the simulated gate remains active and versions have
`gate=simulated`. Both comparisons, n and calibration are version tags. This
fallback is not observed validation. Calibration uses >=100 earlier observed
labels, held apart from a later >=100-label evaluation suffix by the target
window duration; do not report fit-set coverage as held-out coverage.

To inspect/roll back, use the MLflow registry UI or the following **after**
checking the version's run and gate tags. Pin each horizon explicitly; alias
moves are not atomic across three models. Scoring always resolves champion:

```sh
DATABRICKS_CONFIG_PROFILE=transit uv run python - <<'PY'
from mlflow import MlflowClient
client = MlflowClient(tracking_uri='databricks', registry_uri='databricks-uc')
for horizon, version in [(15, '1'), (30, '1'), (60, '1')]:
    name = f'transit.ml.crowding_h{horizon}'
    print(name, client.get_model_version_by_alias(name, 'champion'))
    client.set_registered_model_alias(name, 'champion', version)
PY
databricks bundle run ml_score -p transit
```

Version 1 is the original simulated champion, not necessarily the correct
future rollback target. Prefer the versions in a known-good snapshot.

## Freeze and restore for the demo

```sh
DATABRICKS_CONFIG_PROFILE=transit uv run --with databricks-sdk python scripts/ops/freeze.py --label pre-demo-check
DATABRICKS_CONFIG_PROFILE=transit uv run --with databricks-sdk python scripts/ops/freeze.py --restore-check SNAPSHOT_ID
```

`transit.ml.snapshots` records UTC time, label, public-table → Delta-version
map, replay-table map, champion versions and MLflow run IDs. It captures every
public Silver/Gold table (system `__materialization_*` and `event_log_*` excluded).
Public Lakeflow MVs do not support time travel; each is copied to a dedicated
`snapshot_<id>_<schema>_<name>` Delta table under `ml`, versioned there. Regular
Delta tables use their own history. Captures are table consistent, but not
atomic across all tables. The script fails rather than silently skipping an
unreadable table; a failed capture can leave unregistered copies to clean up
later. Freeze after refresh completion for a coherent demo boundary.

`restore_check` counts every recorded table `VERSION AS OF` the stored version
and fails if any is unavailable. For replay, query the table in `replay_tables`
with that version, e.g. `SELECT * FROM transit.ml.snapshot_<id>_silver_vehicle_pings
VERSION AS OF 0`; use the recorded model versions for prediction replay.
This does not rewind live tables, pipeline checkpoints, or aliases. Never
`RESTORE TABLE` on a pipeline-owned table. Restore a regular table into a
separate replay Delta table with `CREATE TABLE ... AS SELECT ... VERSION AS OF
N` if a demo consumer requires a named copy. Keep originals/history and avoid
VACUUM until after the game/demo; retention policies can invalidate time travel.

## Daily laptop batch (installation is a user action)

```sh
bash scripts/ops/install_launchd.sh --dry-run
bash scripts/ops/install_launchd.sh
launchctl print gui/$(id -u)/com.aggiepulse.daily-ingest
bash scripts/ops/uninstall_launchd.sh --dry-run
bash scripts/ops/uninstall_launchd.sh
```

Launchd invokes `scripts/platform/run_daily_ingest.sh` at 06:00 **system local
time**, with `RunAtLoad=false`. Keep the laptop timezone America/Chicago and it
awake/online at 06:00. Logs: `data/logs/daily_ingest.log`. Launchd coalesces a
missed calendar event on wake; powered-off laptops cannot run tasks. The batch
uses `uv run python -m aggiepulse.ingest all`; the existing sync loop uploads
its closed files. Install does not start ingest immediately or alter the three
continuous loops. No installer was run during this implementation.

## Cost levers

Pause schedules in Databricks Jobs UI when the collection period ends: nightly
ML train (12 Optuna trials/horizon plus refresh), 15-minute ML scoring, 15-minute
health, 15-minute `aggiepulse_ops_refresh` (core then ops), and hourly weather.
Schedules are bundle-owned: persist pause settings in the owning resource file
before the next deploy. Reduce trials through `--trials` for development; reduce
refresh/scoring frequency when latency permits. Do not pause laptop collection
through November 14. Keep core/ops active if relying on Bronze freshness alerts;
pausing pipelines makes health stale. Snapshot CTAS copies add storage/compute;
retain needed demo copies and drop obsolete copies deliberately after use.


## Deployment verification

The latest implementation evidence, exact model metrics, job IDs/schedules,
health rows and snapshot restore counts are in [verification.json](verification.json).
The first scheduled health attempt ran before the new APC view materialized
and failed with a missing-table error; the subsequent manual health run
succeeded. The monitor now persists ERROR rows for upstream query failures.
No collector/sync/publisher was restarted, no launchd agent was installed, and
no commit was made during this verification.
