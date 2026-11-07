# Phase 5: explicitly simulated history and feature semantics

Every synthetic row is `data_origin=simulated`. These are priors for a demo and
software development, not recovered historical APC measurements. Event uplift
must be called a **simulated prior**. Report model metrics on observed holdout
only, with a separate simulated metric line and sample sizes. Nothing here
establishes a causal event effect or validates APC accuracy.

## Inputs and provenance

- **DERIVED FROM DATA:** official GTFS route IDs, actual direction IDs/headsigns,
  route stops and stop coordinates, trip start times, route distances, and
  `calendar_dates` service additions. Current feed has 22 routes / 40 directions,
  valid September 6–December 10, 2026. Calendar additions are used as published;
  the current feed has no weekly `calendar.txt`. Missing service is not invented.
- **DERIVED FROM DATA:** landed Open-Meteo hourly weather, filtered LiveWhale
  events, NTD agency 60278 / MB / DO monthly UPT, and the checked-in academic,
  class-block, venue and home-athletics seeds. Seeds have their own source and
  verification caveats. Their values are not automatically facts about attendance.
- **DERIVED FROM DATA:** measured TripSpark APC windows, flattened by the existing
  collector parser, deduplicated by vehicle/GPS timestamp, with load in [0,1.5].
  Raw direction names must unambiguously match a GTFS headsign on the same route;
  unmatched directions are excluded. Live route RD has no GTFS equivalent.
  Only complete intervals are used. Local GPS timestamps use the collector's
  Chicago/DST handling. No live HTTP calls are made by Phase 5.
- Silver Delta tables are preferred when compatible/nonempty. The same parsers
  provide raw-Volume fallback. Seeds accept direct filenames or content-hash CSV
  files under their stem directories. Raw files are never modified.

## Simulator coefficients

All the following are **OUR ASSUMPTION**, unless explicitly marked derived.

- Grain is every route/direction and UTC-aligned 15-minute interval. Start date
  means Chicago midnight September 6; end is exclusive and floored to the last
  completed quarter-hour. No-service intervals have null load and irregularity,
  zero vehicles and zero pings. An RNG seeded with 42 by default produces noise
  and shortfalls; reproducibility requires identical input snapshots and end.
- **DERIVED FROM DATA:** `sched_trips` counts GTFS **first-stop departures** in
  each window, not all stop-time records. GTFS time is service-day local noon
  minus 12 elapsed hours plus HH:MM:SS; times beyond 24 hours and DST work without
  treating clock time as UTC. Service IDs marked removed are not expanded.
- Capacity is 85, or 25 when route_long_name identifies RELLIS (route 47 in the
  current feed). These fleet-capacity priors agree with observed feed examples;
  they are not a measured fleet assignment for each historical trip.
- Active vehicles approximate departure equivalents: scheduled departures minus
  one with probability 0.06, minimum one when any departure exists. This does
  **not** reconstruct simultaneous fleet/block assignments. Service ratio is
  active vehicles / scheduled departures, null when no departure is scheduled.
- Baseline load as a function of local minute m is
  `0.035 + .23*G(m;510,115) + .20*G(m;735,150) + .25*G(m;1005,130)`, where
  `G(x;mu,sigma)=exp(-0.5*((x-mu)/sigma)^2)`.
- Day multipliers: class 1.00; weekend 0.40; holiday 0.18; break 0.22;
  reading day 0.55; finals 0.65. Priority is holiday, break, reading, finals,
  weekend, class. Thanksgiving follows explicit seed flags, not a date guess.
- Class waves occur on `is_class_day=1` only, using both East and West seed
  blocks with equal weights. For blocks matching M/T/W/R/F, wave is
  `2*(mean G(minutes_to_start;15,10) + mean G(minutes_since_end;5,10))`;
  load contribution is `0.65*wave`. Starts peak 15 minutes before and ends
  5 minutes after. Class timing features use the same blocks. The seed's
  December 1 redefined day uses Friday blocks. No waves on weekends, holidays,
  reading days or finals unless the academic seed explicitly marks classes.
- Observed shape fit uses route-pooled weekend/weekday × local-hour average APC
  load. Blend weight is `n/(n+500)` (500 prior pseudo-windows); observed/prior
  ratio is capped to [0.5,1.5]. Only represented day types/hours change; a
  weekend collection cannot fit weekday or class/event effects. Calibration
  subsequently sets the monthly magnitude. This is a strongly shrunk adjustment,
  not a validated fit. Observation count is logged in run metadata.
- Event contribution is `0.12*event_exposure` from the kernels below. Nearby
  football has large significance and pre/post surges by construction, not from
  learned historical effects. A GTFS window without scheduled service stays
  unserved even on game day; special-event shuttle operations are not invented.
- Rain adds `min(precip_mm/3,1)*0.10` on route-directions whose typical trip is
  shorter than 7 km, otherwise `*0.03`. Simulator uses observed hourly weather;
  missing precipitation is assumed zero **only in the simulator**. Trip length
  is the median maximum GTFS stop-time shape distance per route.
- Route/direction baseline and class contributions have a fixed mean-one
  lognormal multiplier with log sigma .70. Independent window demand multipliers
  are mean-one lognormal with log sigma .35, plus Normal(0,.025) additive noise;
  the multiplicative residual is recorded in sim_noise. These heterogeneity
  priors permit rare crowding windows without claiming observed class balance.
  Raw service-window demand has a .005 floor; the physical [0,1.25] load cap
  is applied **after** monthly scaling, so pre-scaling clipping cannot suppress
  the entire crowding tail. p90 after calibration is
  `min(1.18*average+.035,1.4)`; maximum is `min(p90+.07,1.5)`.
- Headway CV is `clip(.10+.50*average+Normal(0,.04),0,1.5)`; bunching rate is
  `clip(.025+.24*average+Normal(0,.015),0,1)`; mean delay seconds is
  `max(0,30+180*average+Normal(0,25))`. Their load correlation is assumed.
  Raw APC fallback leaves observed CV/bunching/delay null, because pings alone
  do not establish stop-arrival headways or schedule delay.
- Debug-only `sim_baseline/class_wave/event/weather/noise` columns are scaled by
  the monthly factor and zeroed when unserved; `sim_clip_adjustment` reconciles
  clipping. Their sum is the average load (zero for unserved intervals).
  They are deliberately excluded from training features. `n_pings=0` always:
  the simulator never fabricates measured APC pings.

## NTD magnitude calibration

- **DERIVED FROM DATA:** choose the latest nonfuture NTD MB/DO record for the
  matching calendar month. September uses September 2026 UPT **1,158,383**;
  October/November currently use 2025 because 2026 records are unavailable.
  Duplicate source snapshots are deduplicated before selection.
- **OUR ASSUMPTION:** implied gross boardings per departure are average onboard
  load × capacity × **turnover 2.0**. Gross boardings cannot be identified from
  onboard counts; the same monthly UPT can support many load/turnover pairs.
- **OUR ASSUMPTION:** partial months target the fraction of observed quarter-hour
  slots / (calendar days × 96) of monthly UPT. This flat calendar-time allocation
  makes September 6–30 target 25/30 × 1,158,383 = **965,319.17**, with full-month
  equivalent 1,158,383. It does not claim those were actual September 6–30 totals.
  DST contributes actual UTC slots; November's extra hour is disclosed through
  the logged coverage fraction rather than silently discarded.
- Scalar bisection matches that target after the [0,1.25] average-load cap.
  Infeasible targets or missing matching-month NTD raise errors instead of
  silently altering capacity or turnover. Run reports record coverage, source
  month, scale, target, achieved total and relative error. Exact agreement is a
  fitted constraint, not independent validation of simulated occupancy.

## Event exposure

- **DERIVED FROM DATA:** drop rules and category/audience weights are read from
  `event_filters.yaml`. Drop canceled/online/nonlocal/routine service events;
  require a known venue with coordinates or coordinates in the local bbox.
  Venue aliases supply missing coordinates. Unknown locations are excluded.
  Unknown event size remains missing; `unknown_size_events` records active nearby
  unscored events rather than treating their attendance as zero.
- **OUR ASSUMPTION:** significance is `log1p(capacity or rsvp)*max(category_weight)
  *max(audience_weight)`. The YAML weights are modeling priors. Capacity is an
  upper bound, not estimated actual attendance. Seeded athletics use category
  2.5 and audience 1.5; `significance>=10` defines a big event.
- **OUR ASSUMPTION:** H3 resolution 9 / disk radius 3 is a conservative candidate
  prefilter when h3 is installed (4.5.0 installed cleanly). Exact nearest-stop
  haversine distance ≤600 m is always required; distance weight is `exp(-d/300)`.
  If h3 is absent, exact haversine alone produces the same radius semantics.
- **OUR ASSUMPTION:** pre-event kernel is `G(minutes_to_start;45,60)` on [0,180];
  post-event kernel is `G(minutes_since_end;20,40)` on [0,180]; during-event
  weight is .15. Exposures sum across events. Minutes to next/after last big
  event are route-proximity-aware and null when none exists. The football day
  flag uses Chicago date. Venue type is the last active matching event's type;
  numeric exposure, rather than that category, represents overlapping events.
- **OUR ASSUMPTION:** missing event end means four hours for football, two for
  other timed events, 24 for all-day events. Home athletics seeds supplement
  missing calendar instances; duplicate venue/sport/day instances are avoided.
  Missing kickoff uses **14:00 Chicago**, flagged `event_timing_is_assumed=1`.
  Confirmed calendar timing takes precedence over a seed with unknown kickoff.
- Known `published_ts` gates event use at t0. The source lacks historical
  publication snapshots for most events, so `event_schedule_is_hindsight=1`
  explicitly identifies this additional retrospective exogenous-data limitation.

## Training windows and leakage controls

- A source row at `window_start=s` summarizes [s,s+15m). It is available only at
  s+15m. **`load_t0` uses [t0-15m,t0)**; lag15/30/60 use source windows starting
  t0-30/45/75m. Delta30 compares load_t0 with lag30. Exact timestamp joins prevent
  gaps from becoming false 15-minute lags. Missing state remains null. The last
  30-minute headway/bunching mean uses the two completed windows, or one if only
  one is available. Service shortfall is `max(0,1-service_ratio)`.
- Labels use the exact source window [t0+h,t0+h+15m), for h=15,30,60. Crowd label
  is maximum load ≥.75. Missing target remains null, never a negative. Each
  horizon carries `data_origin_h*`; row data_origin comes from a present label
  window. Observed-origin schedule-only rows without any APC/load/headway
  measurement are excluded. At shared keys, measured data replaces the simulated
  prior, preserving route/direction/t0 uniqueness. Target/state joins are isolated
  by origin, so real sparse state is
  never silently filled with simulation. Partial-horizon rows are retained;
  training/evaluation must filter nonnull labels separately per horizon.
- Same-slot last week is seven **local calendar days**, preserving local slot
  across DST (ambiguous prior clock uses the standard-time fold). The seasonal
  baseline is an expanding mean over **simulated training-period windows only**,
  keyed by route/direction/day-type/slot; a window enters only after its end.
  Validation/test/observed values cannot change the profile. After training ends,
  it freezes. Observed evaluation therefore includes a declared simulated prior.
- Default forward split boundaries are 70% and 85% of the simulated time span;
  no random split. Explicit `--train-end` and `--validation-end` are supported
  and should be persisted for repeated comparisons. Defaults move if the data
  range grows. Labels whose complete target windows cross a boundary are purged.
  Every observed label belongs to `observed_holdout`; it never fits the baseline.
- Time features derive from Chicago time, academic seeds and the published class
  schedule. Schedule/static features are published GTFS information. Frequency
  is four times the current quarter-hour's trip starts, not smoothed fleet
  headway. Static fields are median trip length, unique stop count, and stop
  proximity ≤600 m to Kyle Field / MSC.
- Weather selects the latest forecast issue **≤t0** for the current/next hour.
  If no archived forecast exists, an observed hourly substitute is allowed
  **only as the requested explicit exception**, marked by
  `weather_is_observed_proxy` (next hour) and `weather_now_is_observed_proxy`.
  Historical hourly precipitation can itself summarize an unfinished hour at t0,
  hence both flags. These are hindsight features and limit evaluation claims.
  `allow_observed_weather_proxy=False` disables both substitutions. Missing
  weather stays null in features; heat threshold is apparent temperature >35 C,
  rain onset is dry current / positive next-hour precipitation.
- Service-alert feature hooks are nullable pending compatible historical alert
  availability. Never infer "no alert" from unavailable data.
- Exclude all `y_*`, `data_origin*`, `split`, timestamps, and debug/provenance
  columns from model input selection. Route/direction and useful string features
  require explicit categorical encoding. Labels, split and origin are metadata,
  not predictive inputs. No model fitting or metric claims are made in Phase 5.

## Running and lineage

Local development dependencies beyond the project wheel: `h3==4.5.0` and
`PyYAML==6.0.3` (installed with `uv pip install`, leaving shared dependency files
untouched). Serverless environment explicitly declares these plus MLflow.

```sh
uv run python -m aggiepulse.sim.simulate_history --seed 42 --end 2026-11-07T20:00:00Z --output /tmp/history.parquet
uv run python -m aggiepulse.features.build_training_set --history /tmp/history.parquet --output /tmp/training.parquet
uv run python -m aggiepulse.features.job --local-output /tmp/phase5 --end 2026-11-07T20:00:00Z
uv run pytest tests/sim tests/features -q
uv run ruff check src/aggiepulse/sim src/aggiepulse/features tests/sim tests/features
databricks bundle deploy -p transit
databricks bundle run ml_data -p transit
```

The serverless script accepts an explicit --source-root because Databricks
executes Python files without setting __file__; deployed source takes precedence
over any warm-environment wheel cache.

The serverless job overwrites only `transit.gold.route_dir_15m_sim`,
`transit.gold.event_exposure`, and `transit.gold.training_set` with Delta. It
records exact input/output Delta versions, calibration and samples in an MLflow
run under `/Shared/aggiepulse`, with `delta_version.<table>` tags and a
`ml_data_report.json` artifact. Local mode writes the same report and Parquet
frames. The observed `gold.route_dir_15m` is never modified by this job.
