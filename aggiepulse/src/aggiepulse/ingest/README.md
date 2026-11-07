# Batch ingestion

Run from the uv project: `uv run python -m aggiepulse.ingest all`.
Each source also supports `python -m aggiepulse.ingest.<module> --help` and a
`run()` function returning landed Paths. `run_all()` returns per-source results;
the CLI exits nonzero after reporting all sources if any source failed.

Raw JSON is gzip-compressed and wrapped with `ingest_ts`, `query_url`, query
parameters/range, and `payload`. Events are one envelope per JSONL line. Weather
also includes `kind`. GTFS remains a valid ZIP with a timestamp-matched metadata
sidecar. Exclusive file creation prevents overwrites. Paths use the shared
`AGGIEPULSE_DATA_ROOT` configuration and UTC date partitions. Requests use the
shared descriptive User-Agent, connection/read timeouts, and exponential retries.

Parsers accept decoded envelopes, perform no I/O, and return pandas DataFrames
with UTC timestamps and nullable numeric/string/boolean dtypes. Events take an
iterable of envelopes; other parsers take one. NWS missing quantities remain null;
metre precipitation is converted to mm. NTD monthly dates are UTC month starts.

## Verified live on 2026-11-07

- GTFS: 22 routes, 203 stops, **6,312** trips, 57,870 stop times. The plan's
  trip estimate was 6,313. Every `.txt` table is read with string dtypes.
- LiveWhale: 8,293 records across 19 inclusive, nonoverlapping weekly chunks,
  2026-08-01 through 2026-12-07; 6,577 distinct IDs. Recurring events share IDs:
  downstream deduplication must include occurrence start time. No chunk hit 1,000.
  Capped chunks are recursively bisected; a capped single day is landed and logged
  as potentially incomplete. Historical pulls are explicit, for example
  `python -m aggiepulse.ingest.events_livewhale --range 2024-08-01 2024-12-31`.
  Campus arrays use `event_types_campus`; actual values include Bryan-College
  Station and multiple remote campuses, plus a trailing Unicode separator on Dallas.
- Open-Meteo: default best-match archive currently supplies the current day,
  including hours later than fetch time. These are provider archive products,
  not a guarantee of finalized station observations. Final fetch: 2,376 archive
  hours, Aug 1 through Nov 7; forecast: 384 hours, Nov 7 through Nov 22.
  An earlier archive through Nov 2 and forecast snapshot are retained append-only.
- NWS: 500 observations, Nov 3 15:50 UTC through Nov 7 19:05 UTC.
- NTD: supplied dataset **8bui-9xvu is correct**:
  https://data.transportation.gov/resource/8bui-9xvu.json?ntd_id=60278
  Fields are `date`, `upt`, `vrm`, `vrh`, `voms`, `mode`, `tos`, `ntd_id`.
  25 rows span Sep 2024 through Sep 2026. Summary aggregates across mode/service
  types and prints the latest 24 available months. Pagination orders by date,
  mode, tos and Socrata row ID for deterministic offsets.

Trimmed real responses are in `tests/ingest/fixtures`. Verification:
`uv run pytest tests/ingest -q` and
`uv run ruff check src/aggiepulse/ingest tests/ingest`.
