# AggiePulse operations

Owned code: `src/aggiepulse/transform`, `pipelines/ops`,
`resources/pipeline_ops.yml`, `tests/transform`. No raw data is changed.

## Running

```sh
uv run pytest tests/transform -q
uv run ruff check src/aggiepulse/transform pipelines/ops tests/transform
uv run python -m aggiepulse.transform.local
databricks bundle validate -p transit
databricks bundle deploy -p transit
```

The local run reads immutable snapshots of the currently readable gzip members,
parses GPS timestamps through `collector.parse`, deduplicates and applies the
core's basic freshness/location/APC checks. Its summary is `local_summary.json`.
An actively appended gzip tail can be incomplete; rerun for newer observations.

Optional full Spark execution and pandas stop-visit parity check (JDK 17/21):

```sh
uv run --with pyspark==4.0.1 python tests/transform/spark_check.py
```

This check executes real Spark DataFrames and pandas UDFs; only Lakeflow's
registration decorators are replaced. It does not test Databricks permissions,
serverless dependencies or Lakeflow scheduling. It uses no driver actions in
production dataset definitions and requires no local Spark dependency in uv.lock.

## Production inputs and outputs

Inputs are the fixed `transit.silver.vehicle_pings`, `gtfs_*`, and
`schedule_expanded` contracts. Routes and directions come from GPS shape fitting;
`tripspark_routes` is not required because the clean pings already carry names.

Published views are `silver.route_direction_map`, `silver.stop_visits`,
`silver.headways`, `gold.route_dir_15m`, and `gold.stop_15m`. Every output carries
`data_origin`. No observed/simulated metrics, lag calculations, fits or counts
are mixed. UTC is the Spark session timezone and the 15-minute alignment clock.

The route map retains every candidate with `candidate_rank`, `is_selected`,
`median_offset_m`, `backward_fraction`, `fit_quality` and `n_pings`. Quality is
`exp(-median_offset_m / 50)`, a descriptive score rather than a probability.
The primary selected direction is shared across a vendor route/direction key;
within that direction a vehicle run can select another better-fitting shape.

Static reference arrays are compacted in Spark and attached once per packed
vehicle-day group. The longest stop pattern per shape is used, with trip ID as
a deterministic tie-break. Per-service-day schedule arrays accompany groups for
trip matching. No driver collection or actions occur in production definitions.
At larger history volumes these per-day arrays and full refreshes should be
replaced with incremental vehicle-day processing and route/day schedule joins.

## Definitions and limitations

- Coordinates use a local equirectangular metre frame. Shape distances from GTFS
  are converted by interpolating the feed's own shape-vertex distance scale.
  Missing distances fall back to ordered geometric stop projection.
- Median perpendicular distance chooses candidate shapes; directed progress
  breaks geometric ties within 5-metre score bands. Exact ties resolve
  deterministically, so matching reversed/shared geometry without movement is
  uncertain even if perpendicular fit is excellent.
- Projection permits 40 m backward jitter, clamps it to prior progress, and
  rejects larger reversals, offsets over 100 m and travel faster than 35 m/s
  plus 40 m positional tolerance. Gaps over 180 seconds start a new segment.
  Loops close within 80 m and unwrap progress across the seam. Near-identical
  terminal crossings at the two pattern endpoints are collapsed.
- Stops crossed between accepted pings get linear timestamps at microsecond
  precision. Stationary pings (vendor speed <=0.5) within 30 m along and across
  the shape extend arrival/departure to bracket dwell. The unknown vendor speed
  unit is not used for travel speed. Dwell outside the sampling interval, stops
  just before/after a vehicle-day boundary, skipped stops and mid-run branch
  changes cannot be resolved reliably. A near-stop dwell with no observed
  crossing is not promoted into a fabricated visit.
- APC bracketing values produce `net_board_proxy = after - before`. This is a
  signed proxy, not boarding/alighting demand. Several stops crossed in one
  polling interval can share the same APC change; summing proxies is not a
  conservation law and can double attribute that change. Missing APC stays null.
- Trip matching requires two ordered timepoints in the same trip/service-date
  instance, median delay within 15 minutes, delay standard deviation <=180 s,
  and a 60-second advantage over the next candidate. Ambiguity stays null.
  Local schedule expansion follows GTFS noon-minus-12-hours semantics across DST;
  production consumes the core's expanded UTC schedule.
- Headways lag observed arrivals within stop/route/direction/origin. Scheduled
  headway is the interval at the closest scheduled arrival within one hour.
  No prior observation or unavailable schedule gives null flags. Simultaneous
  distinct vehicles give zero observed headway and can count as bunching.
- `sched_trips` counts distinct scheduled trip instances active in each window
  from their first through last stop, inclusive of endpoint windows. It does not
  count stop_times rows. `service_ratio = active_vehicles / sched_trips` is thus
  a service-coverage proxy, not a timetable cancellation measure. Schedule-only
  rows have `n_pings=0`, with observed values and active vehicle count null.
- `headway_cv` is sample standard deviation / mean of arrivals' headways in the
  route/direction/window; it is null with fewer than two headways. Bunching rate
  excludes unknown flags. `max_passing_load` is a load factor observed within
  30 m of the stop, not an onboard passenger count. P90 uses the same continuous
  percentile definition in pandas and Spark.

## Deployment and refresh

The triggered, serverless pipeline is `aggiepulse_ops`, catalog `transit`.
It uses the runtime-provided pandas, NumPy and Arrow packages; installing broad
version ranges through pipeline environment pip conflicted with runtime constraints.
The quarter-hour job `aggiepulse_ops_refresh` explicitly runs the core refresh
job, waits for success, then refreshes ops. The first successful ops update is
`30f9f428-3727-452d-bca2-c7fd12ed2ecf`; its state is `COMPLETED`. The quarter-hour
job is **UNPAUSED** after that validation. A manual refresh is available through
`databricks bundle run aggiepulse_ops -p transit`.
Core also has an independent periodic refresh; the coordinator may choose a
single scheduler to avoid duplicate core refreshes. This workstream does not
modify the core resource.
