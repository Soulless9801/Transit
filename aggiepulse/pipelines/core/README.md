# AggiePulse core pipeline

`resources/pipeline_core.yml` defines one triggered, serverless Lakeflow pipeline,
`aggiepulse_core`, publishing explicitly to `transit.bronze` and `transit.silver`.
The serverless `aggiepulse_core_refresh` job runs at minutes 00/15/30/45 UTC. Its
first task stages GTFS and seeds; the second refreshes the pipeline. No ops/gold
tables are owned here.

## Deployment and validation

From the package root:

```sh
uv run pytest tests/pipeline_core -q
uv run ruff check pipelines/core scripts/pipeline_core tests/pipeline_core
databricks bundle validate -p transit
databricks bundle deploy -p transit
databricks bundle run aggiepulse_core_refresh -p transit --no-wait
uv run python scripts/pipeline_core/status.py 206b4370-b5cc-48eb-8eba-932dcea5dc95
uv run python scripts/pipeline_core/report.py
```

The report contains exact counts for every core table, vehicle quality pass
counts, time range, route mismatches, filter reasons and sample rows. To stage
without waiting for job compute:

```sh
uv run --with pyyaml python scripts/pipeline_core/prepare.py --local
```

The bundle syncs the vendored `lib` helpers; a configured workspace path supports
pipeline runtimes that do not define `__file__`. Heavy transforms use Spark.

## Data contracts and decisions

- JSONL/gzip Bronze inputs use Auto Loader's text reader, preserving the payload
  as JSON text. Missing payload/ingest timestamps retain the whole envelope in
  `_rescued_data`. NTD expands one raw dataset record per Bronze row. CSV columns
  remain strings, with Auto Loader schema rescue enabled. File provenance and
  ingestion time are retained.
- The preparation task extracts only the newest ZIP. It uses a versioned
  SHA-256 content identity and immutable `gtfs_extracted/v2_<hash>/<table>/*.csv`
  files, preserving identifiers such as `0012`. The publisher's feed_version in
  feed_info is retained as `published_feed_version`. The `v2_` namespace excludes
  initial development staging files with a duplicate feed_info header.
- Seeds are copied to `/Volumes/transit/raw/seeds/*.csv` and event_filters.yaml.
  Separate immutable hash-named seed inputs preserve Bronze history. Current
  Silver seeds select the latest ingested revision. Policy is YAML normalized to
  JSON by preparation, then read through Bronze. The task's additional explicit
  study-session title exclusion supplements the seed rules.
- Silver GTFS tables select the current feed. Stop times retain HH:MM:SS and
  expose integer `arrival_time_seconds`, `departure_time_seconds` and aliases
  `arrival_secs`, `departure_secs`. Values beyond 24:00 stay beyond 86400.
  Schedule timestamps anchor at local service-date noon converted to UTC, then
  add GTFS seconds minus 43200 **as elapsed seconds**. This handles both DST days.
- Vehicle GPS fold selection matches collector/parse.py: the latest valid fold
  at or before ingestion; nonexistent local timestamps become null/quarantine.
  The timestamp UDF is small; payload flattening, cross-collector deduplication,
  route enrichment, H3 and exact broadcast nearest-stop search are Spark-native.
  The earliest valid ingestion wins a duplicate `(vehicle_id, ts_gps)` key.
- `vehicle_pings_candidates` records expectation metrics before dropping rows.
  `vehicle_pings_quarantine` retains every failing candidate with all failing
  rule names. Published pings require identity, local bbox, positive capacity,
  0–1.5x capacity onboard, GPS age under 300 seconds and a route key. Up to 60
  seconds of source clock skew into the future is tolerated. The observed label
  is fixed. Speed is preserved in vendor units, which remain undocumented.
- `vehicle_latest` is a materialized snapshot of the newest ping per vehicle in
  the last ten minutes **at refresh time**. The app should still gate freshness
  at request time because pipeline refreshes are 15 minutes apart.
- Route metadata is flattened from the most recent successful base poll.
  `tripspark_route_mismatches` reports short names absent from current GTFS.
  `patterns` retains direction pattern keys/display flags. Alerts use route
  references in the base payload, strip HTML, and map only explicit closed-stop
  lists to GTFS IDs; temporary and disruption-boundary stops are excluded.
- Events apply seed drop rules first; physical local evidence is required even
  when the campus says College Station. Known venue aliases fill missing GPS.
  Category/audience arrays are HTML-decoded. Unknown capacity and RSVP yield
  null attendance/significance. Significance uses the plan's log1p-size formula
  and maximum matching weights. Capacity is an upper-bound proxy, not observed
  attendance. Cancellation supersedes older kept occurrences. Calendar
  instances deduplicate on `(event_id, start_ts)`.
- Athletics safety-net seeds add missing home football and basketball games,
  with sport/team/venue/local-date suppression against existing calendar rows.
  A canceled calendar game is not reintroduced by a seed. Unknown kickoffs are
  all-day local events, not fabricated exact start times; known game durations
  use explicit 4h football/3h basketball modeling assumptions.
- Filter statistics count raw calendar rows by first matched rejection reason;
  they exclude synthetic seed additions and count recurring occurrences.
- Weather retains the latest hour per source. Open-Meteo UTC offsets are
  respected; forecasts carry their landing issue timestamp (a retrieval-time
  proxy). NWS metre precipitation converts to mm; weather_code/rain remain null
  when unavailable. Apparent temperature falls back through heat index, wind
  chill, then temperature. NTD grain is month/agency/mode/service type.

## Known limits

No exact kickoff is fabricated for TBA games. Venue coordinates/capacities remain
missing wherever seeds mark them unverified; matching an alias does not invent
coordinates. Category/audience weights and game durations are modeling priors.
Historical Open-Meteo archive products can include not-yet-final current-day
hours; the source label does not certify a finalized station observation.
Silver materialized views guarantee replay deduplication but full-history window
and nearest-stop computations should be profiled as history grows. GTFS feed
changes replace current Silver schedules; Bronze preserves earlier versions.
