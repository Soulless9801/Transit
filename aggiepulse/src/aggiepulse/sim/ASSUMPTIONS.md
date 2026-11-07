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
- Silver Delta tables are preferred when compatible/nonempty, **except
  `silver.events`** (Phase 6): it gives sizes/coordinates to events this policy
  excludes or leaves unsized, which on 2026-11-07 put median route event exposure
  at 14.8 (0 under the policy) and let the event prior dominate simulated demand
  and the NTD scale. Events always come from raw LiveWhale envelopes through
  `prepare_events`. The same parsers
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
  blocks. For blocks matching M/T/W/R/F, wave is
  `(sum G(minutes_to_start;15,10) + sum G(minutes_since_end;5,10)) / n_regions`
  (sum over that day's blocks, divided by the number of campus regions in the
  seed, 2). A changeover in one region therefore peaks near 1.0. **Phase 6
  change (OUR ASSUMPTION):** this replaces `2*(mean + mean)` over all blocks,
  which divided every class change by the ~16 blocks in a day and capped the
  wave at ≈0.22 regardless of how many students change class at once. Load
  contribution is `CLASS_WAVE_COEF (1.0) * wave * route class-change weight`
  (see route groups below). Starts peak 15 minutes before and ends
  5 minutes after. Class timing features use the same blocks. The seed's
  December 1 redefined day uses Friday blocks. No waves on weekends, holidays,
  reading days or finals unless the academic seed explicitly marks classes.
  Because East and West timetables are offset by ~30 min, MWF days have a class
  change in one region roughly every 30-40 minutes; TR days show clearer gaps.
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
- **Phase 6 change (OUR ASSUMPTION): route groups replace random route
  weights.** Before, each route/direction drew a mean-one lognormal (log sigma
  .70) multiplier from the shared RNG, so *which* routes were busy depended on
  input row order (local and Databricks runs gave different busy routes) and
  heavy demand was not tied to campus structure. Now the group comes from the
  published GTFS names: `on_campus` = 01, 03, 04, 05, 06, 08 (TAMU's 0x
  on-campus numbering); `off_campus` = other numbered routes; `night_weekend` =
  combined codes (01-04, 03-05, 40-41, "Break, Nights, & Weekends"); `airport`
  (07); `rellis` (47). Weights (baseline, class-change): on_campus (1.0, 1.0),
  off_campus (1.0, .30), night_weekend (.80, .15), airport (.50, .05),
  rellis (.80, .15). Rationale: the class-change wave is intra-campus movement,
  so it is carried mostly by on-campus circulators; off-campus routes carry the
  commute peaks already in the baseline curve. GTFS frequency (derived from data)
  already concentrates departures on a few routes. No public per-route TAMU
  ridership was found, so the group split is a prior, not a fit. Result on the
  Sep 6–Nov 7 run: on-campus routes carry ≈47% of simulated boardings,
  off-campus ≈51%, the rest ≈2%.
- Each route/direction keeps a small mean-one lognormal residual (log sigma
  .25) drawn from an RNG keyed by `crc32(route|direction)` and the seed, so it
  is independent of input order.
- **Phase 6 change (OUR ASSUMPTION):** a shared route/direction × local-day
  mean-one lognormal demand level (log sigma .20) multiplies the window demand.
  Real demand varies day to day (exams, weather beyond the rain term,
  unscheduled events); purely independent window noise implies every weekday is
  identical in expectation. Independent window demand multipliers remain
  mean-one lognormal with log sigma .35, plus Normal(0,.025) additive noise;
  the multiplicative residual is recorded in sim_noise.
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
  load × capacity × **turnover 1.5** (Phase 6; was 2.0). Gross boardings cannot
  be identified from onboard counts; the same monthly UPT can support many
  load/turnover pairs. Cross-check (derived from data): GTFS-scheduled revenue
  miles for October 2026 (58,121 departures × median trip length = 204,529 mi)
  match NTD October 2025 VRM (201,251 mi) within 2%, so the service volume is
  right and turnover is equivalent to an average passenger trip length
  L ≈ 3.52 mi / turnover. Turnover 2.0 implies L ≈ 1.8 mi; 1.5 implies
  L ≈ 2.3 mi. Both are plausible for a campus system whose ridership is
  dominated by apartment-to-campus trips ridden most of a route's length; the
  monthly NTD feed has no passenger-miles to pin it down. Sensitivity (same
  seed, all Phase 6 changes): turnover 2.0 gives 0.29% crowded class-day
  windows; 1.5 gives 1.15%.
- **OUR ASSUMPTION:** partial months target the **day-type-weighted** share of
  monthly UPT (Phase 6; was flat calendar time): each local date gets its
  simulator day multiplier (class 1.0, weekend .40, …) and the target is
  Σ covered-slot weights / Σ weights of all days in the month. Flat allocation
  gave the first week of November 7/30 of a month that contains Thanksgiving
  break, an artificial level drop in the test split. September 6–30 now targets
  81.95% × 1,158,383 = **949,322.90**; November 1–7 25.1% × 764,116 = 191,792.78.
  Full-month equivalents still equal NTD UPT exactly. `calibrate()` keeps the
  flat rule when no day weights are passed. DST contributes actual UTC slots.
- Scalar bisection matches that target after the [0,1.25] average-load cap.
  Infeasible targets or missing matching-month NTD raise errors instead of
  silently altering capacity or turnover. Run reports record coverage, source
  month, scale, target, achieved total and relative error. Exact agreement is a
  fitted constraint, not independent validation of simulated occupancy.

## Phase 6 crowding diagnosis (why y_crowd was ≈0.04%)

Before Phase 6, the Databricks training set had 34 crowded (max load ≥ .75)
windows out of 80,995 served simulated windows (0.04%; 34 positive
`y_crowd_h30` labels). Crowding needs average load ≥ .547 under the p90/max
mapping, ≈5× the NTD-implied mean. Causes, in order of effect:

1. The class wave was a mean over every block of the day, so a class change
   added at most ≈0.22 × .65 ≈ .14 raw load, then ×≈.27 monthly scale ≈ .04.
   It was nearly flat across the day, so there was no class-change peak.
2. Route weights were random (sigma .70) and input-order dependent: the Databricks
   run spread them evenly (max route mean load .18), while a local run with
   identical inputs put most crowding on route 36 (0.6% overall). Busy routes
   were RNG luck, not campus structure.
3. Turnover 2.0 sets the NTD-implied mean load at ≈.11 per departure; with
   turnover 1.5 it is ≈.15. The tail is very sensitive to this unidentified
   factor (see sensitivity above).
4. The p90/max mapping (`p90 = 1.18·avg + .035`, `max = p90 + .07`) was kept:
   the 25 observed weekend windows available on 2026-11-07 show max/avg ≈ 1.20, consistent with it (with
   tiny loads and one bus per window, so this is weak evidence).

After Phase 6 (same seed, Sep 6–Nov 7 20:00 UTC, local inputs): 1.12% of all
served windows, 1.15% of class-day windows, 3.0% of class-day on-campus windows,
5.9% of on-campus windows at a class change (wave > .6), 0.29% of class-day
off-campus windows and 0.34% of weekend windows (football/events) are crowded;
907 positive `y_crowd_h30` labels. NTD agreement is exact by construction (relative error < 1e-15
for every month). These rates are targets of our assumptions, chosen to be
plausible for packed class-change buses; they are not measurements, and they
were set before any model was trained (the model was not used to tune them).

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
- Note: every current route has a stop within 600 m of both Kyle Field and the
  MSC, so `serves_kyle_field`/`serves_msc` are constant (uninformative) today.
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
  s+15m. **`load_t0` and `p90_load_t0` use [t0-15m,t0)**; lag15/30/60 use source windows starting
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

## Inference mode (Phase 6 scoring)

- `build_inference_features` reuses `assemble_features`, the exact code path of
  the training builder: one row per route/direction with GTFS-scheduled service
  in a target window, recent state only from completed **observed** APC windows
  ending ≤ t0, `scheduled_trips_t0` from the published schedule, and the frozen
  train-only seasonal profile (`gold.seasonal_profile`, written by the ml-data
  job and embedded in each registered model). A test asserts the two builders
  produce identical features for the same t0.
- The scorer sets t0 to the end of the newest completed observed window (never
  later than the current quarter-hour; falls back to the quarter-hour if APC is
  more than 60 min behind). `feature_staleness_min` = minutes from scoring time
  back to the end of that route/direction's newest observed window (24 h
  lookback); null when there is none.
- Live weather uses forecasts issued ≤ t0 when they exist, so the observed
  proxy flags differ from training (where they are mostly 1). The proxy and
  hindsight flags are therefore excluded from model inputs.

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
`transit.gold.event_exposure`, `transit.gold.training_set` and
`transit.gold.seasonal_profile` with Delta. It
records exact input/output Delta versions, calibration and samples in an MLflow
run under `/Shared/aggiepulse`, with `delta_version.<table>` tags and a
`ml_data_report.json` artifact. Local mode writes the same report and Parquet
frames. The observed `gold.route_dir_15m` is never modified by this job.
