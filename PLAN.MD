# AggiePulse — Implementation Plan

*Research done 2026-11-07 (Saturday). Every endpoint below was called from this machine on that date unless marked otherwise.*

---

## 0. Findings that shape the plan

1. **Real passenger counts are available live.** TAMU's rider site (TripSpark, `aggiespirit.ts.tamu.edu`) returns `passengersOnboard` and `passengerCapacity` for every vehicle in its vehicle feed. Three polls 20 s apart showed the counts changing (e.g. 0→3, 14→15, 6→7), so these are live automatic-passenger-counter (APC) readings, not placeholders. **Crowding can therefore be a measured target, not only a proxy.**
2. **There is no history.** The live feed only shows the current moment, and no public archive exists. Every hour before the collector starts is data lost for good. **Starting the collector is task #1 (hour 0).**
3. **TAMU publishes an official static GTFS feed** (22 routes, 203 stops, 6,313 trips, timepoints, shapes; valid 2026‑09‑06 → 2026‑12‑10). Its stop codes match the live feed exactly (203/203), so the two join cleanly. That makes schedule delay and headway deviation computable.
4. **TAMU reports to the National Transit Database** (NTD ID 60278), which publishes monthly boardings (e.g. Sep 2024: 1,003,376 unlinked passenger trips). That is real ground truth for calibrating any simulated history.
5. **The events calendar has a JSON API with history.** `calendar.tamu.edu` runs on LiveWhale, and date-range queries for Sep 2024 and Sep 2025 both returned events. It is noisy (other campuses, library study sessions, Zoom events) and needs filtering.
6. **A natural demo moment falls a week out:** Football vs Tennessee at Kyle Field on **Sat 2026‑11‑14** is in the calendar. Today (11‑07) is an away game, so the hackathon weekend itself has light, non-event service. The live feed also announces detours for Nov 9 and Nov 12, 4:30–10 PM, which are known upcoming disruptions to forecast against.

---

## 1. Data-source verification

| # | Source | Endpoint | Verified | Granularity / coverage | History | License / terms | Role |
|---|---|---|---|---|---|---|---|
| 1 | **TAMU GTFS (static)** | `https://transport.tamu.edu/webFS/gtfs/google_transit.zip` (listed in Transitland atlas as `f-tamu~tx~us`) | ✅ 200, 1.09 MB | 22 routes, 203 stops, 6,313 trips, 57,870 stop_times (timepoint flags), 2,414 shape pts, 6 service_ids via `calendar_dates` | Current term only (feed_version "FY27 FALL 2026 NIGHT/WEEKENDS v3.0") | Published for Google Transit; no license file → treat as public schedule data, attribute TAMU Transportation Services. Contact in feed: `<redacted-email>` | Schedule, shapes, stops, scheduled headways |
| 2 | **TripSpark live vehicles** | `POST aggiespirit.ts.tamu.edu/RouteMap/GetVehicles/` | ✅ 200 | Per vehicle: `name`, `lat/lon`, `speed`, `heading`, `lastGpsDate` (**local time, no offset**), `passengersOnboard`, `passengerCapacity` (85 for 40 ft buses, 25 for the RELLIS shuttle), `isExtraTrip`, route/direction keys. The site's own map polls every 15 s | **None** — live only | Undocumented, no published API terms; `robots.txt` does **not** disallow `/RouteMap` | **Primary**: positions + measured load |
| 3 | TripSpark base data | `POST /RouteMap/GetBaseData/` | ✅ | 23 routes (incl. `RD` Ring Day), directions, patterns, **current + upcoming service interruptions** with UTC windows and affected routes/stops | Current/upcoming only | Same as #2 | Route metadata, **service alerts** |
| 4 | TripSpark pattern paths | `POST /RouteMap/GetPatternPaths/` | ✅ 322 KB | Polylines with stop sequence per pattern | — | Same | Cross-check with GTFS shapes |
| 5 | TripSpark stop ETAs | `POST /Schedule/GetStopEstimates` | ✅ (works) | Realtime departure estimates per stop; `scheduledDepartTimeUtc` came back empty | — | ⚠️ **`robots.txt` disallows `/Schedule`** | **Don't collect.** Compute ETAs ourselves |
| 6 | **NTD monthly ridership** | `data.transportation.gov/resource/8bui-9xvu.json?ntd_id=60278` | ✅ | Monthly UPT, VRM, VRH, VOMS (bus, directly operated) | Multi-year | US Gov, public domain | Calibrate simulated volume; sanity checks |
| 7 | **TAMU events (LiveWhale)** | `https://calendar.tamu.edu/live/json/events/` and `…/start_date/YYYY-MM-DD/end_date/YYYY-MM-DD/` | ✅ default returns 1,000 events (~10 days); date ranges work for 2024 and 2025 | `date_utc/date2_utc`, `location_title`, lat/lon (**only ~25% have coordinates**), `event_types`, `event_types_audience`, `event_types_campus`, `tags`, `group_title`, `rsvp_total`, `is_canceled` | ✅ 2024+ | Public calendar | Event exposure features |
| 8 | Athletics | Inside #7 ("Sports & Athletics"; e.g. *Texas A&M University Football vs Tennessee*, *Aggie Football – Black Out \| Kyle Field*, 11‑14) | ✅ in calendar | Same as #7 | ✅ | Public | Football/basketball home-game flags. Add a small hand-made CSV of the home schedule as a safety net |
| 9 | **Weather — Open-Meteo** | `archive-api.open-meteo.com/v1/archive` + `api.open-meteo.com/v1/forecast` (30.61, −96.34) | ✅ archive | Hourly temp, precipitation, wind, weather code; forecast 1–16 days | Decades | CC BY 4.0, free non-commercial → attribute | History backfill + forecast features |
| 10 | Weather — NWS | `api.weather.gov/stations/KCLL/observations` (Easterwood Airport) | ✅ 500 obs back to 11‑03 | ~20–60 min observations | ~7 days | Public domain; requires a User-Agent | "Now" conditions. Some fields can be absent (e.g. `precipitationLastHour`), so parse defensively |
| 11 | Academic calendar | LiveWhale "Academic Calendar" event type (237 entries in the next 10 days) | ✅ | Term milestones | ✅ | Public | Plus a hand-built `academic_days.csv` (first/last class day, Thanksgiving, reading days, finals). `registrar.tamu.edu/academic-calendar` returned 404, so find the current URL |
| 12 | Class period start times | Registrar class schedule grid | ❌ not fetched | MWF and TR start-time blocks | — | Public | Hard-coded seed, **check against the registrar before use**; drives the "class change wave" feature |

**Not available:** official GTFS-Realtime; per-stop boardings/alightings (only onboard load, so net change at a stop is inferable but gross boardings are not); trip IDs on vehicles (must be inferred); any historical vehicle or APC archive.

### Live-feed access recipe (verified)
```
1. GET  https://aggiespirit.ts.tamu.edu/RouteMap
     → cookies: TSAGGIESPIRIT, .MyRide.RequestVerificationToken
     → inline script: RouteMap.init(routeMapOptions, <fn>, "<BASE64>", false)
2. token = base64decode(<BASE64>)
3. POST /RouteMap/GetBaseData/           header RequestVerificationToken: token, body ""     (send Content-Length)
4. POST /RouteMap/GetVehicles/           form body: routeKeys[]=<guid>&routeKeys[]=<guid>…  (all 23 routes, one call)
5. On HTTP 4xx/5xx or an HTML response → repeat step 1 (new session).
```
**Politeness budget:** one `GetVehicles` call every 30 s for all routes = 2,880 calls/day. That is less than one student leaving the map open (every 15 s). `GetBaseData` hourly. Set a descriptive User-Agent, and email `<redacted-email>` to tell them about the project.

---

## 2. Prediction targets that can be trained and evaluated credibly

| Target | Type | Ground truth | Credibility | Decision |
|---|---|---|---|---|
| **T1. Route-direction crowding**: p90 load factor (onboard ÷ capacity) in the window [t0+h, t0+h+15 min], h ∈ {15, 30, 60} | Regression (quantile) | **Measured** (APC) | High, but vendor APC accuracy is unvalidated. Label it "APC-reported" | **Primary** |
| T1b. Crowding risk: P(max load factor ≥ 0.75 in window) | Binary classification | Derived from T1 | High; class is rare → evaluate with PR-AUC | Primary (UI shows this) |
| **T2. Service irregularity**: headway coefficient of variation per route-direction-window; bunching rate (observed/scheduled headway < 0.5) | Regression | **Derived** from GPS-detected stop arrivals + GTFS | Medium-high; ±15 s interpolation error at 30 s polling | **Secondary** |
| T3. Schedule delay at timepoints | Regression | Derived; needs vehicle→trip matching | Medium (matching heuristic) | Stretch |
| T4. Stop-level demand | — | **Not measurable.** Proxy = Δonboard across a stop visit ("net boarding proxy") | Low; label it a proxy | Display only, no model |
| T5. "Event impact" | Counterfactual | Model prediction with event features vs with them zeroed | It is an attribution, not a causal estimate | UI explanation only |

### The history problem and how to handle it honestly
- **Real data:** whatever the collector gathers from hour 0. A 36 h hackathon starting Saturday yields mostly weekend and light-service data (9 buses were active at 1 PM today). **Recommended:** keep the collector running through 11‑14 (the home game) even after the hackathon.
- **Simulated backfill (clearly flagged):** generate Fall 2026 history (09‑06 → now) at route-direction-15 min grain from:
  - real inputs: GTFS schedule, Open-Meteo weather, LiveWhale events, academic calendar;
  - calibration: total monthly volume matches NTD UPT magnitude, and the time-of-day profile is fitted to the real collected data.

  Every row carries `data_origin ∈ {'observed','simulated'}`.
- **Rules:**
  1. All reported metrics come from a **held-out window of observed data only**. Report a separate metric line for simulated data so the gap is visible.
  2. In the simulator, event effects are *our assumptions*. Do not claim the model "discovered" them. The event-impact UI shows a "simulated prior" badge until observed event data exists.
  3. The UI tags every number as **Measured**, **Predicted**, or **Simulated**.

---

## 3. Architecture

```
                    ┌────────────────────── Databricks workspace (Unity Catalog: aggiepulse) ──────────────────────┐
 TripSpark live ──► │ Job: collector (serverless, continuous)  ──► UC Volume /Volumes/aggiepulse/raw/landing/...      │
 (30 s, 1 POST)     │   tripspark_vehicles/, tripspark_base/ (hourly)                                               │
 GTFS zip (daily) ─►│ Job: batch_ingest (daily) ─ gtfs, events (−7..+30 d), weather, ntd ──► Volume                 │
 LiveWhale, Open-   │                                                                                               │
 Meteo, NWS, NTD    │ Lakeflow Declarative Pipeline  (Auto Loader → BRONZE → SILVER → GOLD, with expectations)      │
                    │                                                                                               │
                    │ Job: train (on demand) ─ MLflow experiments ─► UC Model Registry  aggiepulse.ml.crowding@champion│
                    │ Job: score (every 5 min) ─ batch inference + SHAP contributions ─► gold.predictions          │
                    │                                                                                               │
                    │ SQL Warehouse (serverless) ◄── Databricks App (FastAPI + React/MapLibre)                     │
                    │ AI/BI Dashboard (ops view) · Genie space (natural-language questions over gold)              │
                    └───────────────────────────────────────────────────────────────────────────────────────────────┘
 Backup collector: same script on a laptop, writing JSON lines locally → upload to the Volume (guards against workspace/job outages).
```

**Technology choices (recommended):**
- **Ingestion:** Python `requests` collector, writing gzip JSON lines to a UC Volume. Auto Loader (`cloudFiles`) reads them into Bronze.
- **Transforms:** a Lakeflow Declarative Pipeline (DLT) in PySpark. Expectations enforce quality, and Unity Catalog lineage comes for free.
- **Geospatial:** Databricks built-in H3 functions (`h3_longlatash3`, `h3_kring`, `h3_distance`) for event-to-route exposure and stop proximity.
- **ML:** LightGBM, MLflow tracking, models registered in UC with aliases, Optuna for a small hyperparameter sweep.
- **Serving:** batch scoring every 5 min into a Delta table. This is simpler and more robust than a real-time endpoint for a demo. Add a Model Serving endpoint only for the "what-if" slider, as a stretch.
- **App:** Databricks Apps (FastAPI backend that queries the SQL warehouse, with a 15 s in-memory cache) and a React + Vite + MapLibre GL + deck.gl frontend; no map token needed. *If the team has no frontend developer, use Streamlit + pydeck in the same Databricks App instead.*

---

## 4. Data schemas

All timestamps are stored as UTC `TIMESTAMP`. Local time comes from `America/Chicago`. The live feed's `lastGpsDate` is local time without an offset, so convert it on ingest.

### Bronze (raw, append-only)
| Table | Columns |
|---|---|
| `bronze.tripspark_vehicles` | `poll_id STRING, ingest_ts TIMESTAMP, http_status INT, payload VARIANT` (or STRING), `_source_file` |
| `bronze.tripspark_base` | `ingest_ts, payload VARIANT` (routes, directions, service interruptions) |
| `bronze.gtfs_{routes,stops,trips,stop_times,shapes,calendar_dates,feed_info}` | Raw CSV columns + `feed_version, ingest_ts` |
| `bronze.events` | `ingest_ts, query_start, query_end, payload VARIANT` (one LiveWhale event per row) |
| `bronze.weather_openmeteo` | `ingest_ts, kind ('archive'/'forecast'), payload` |
| `bronze.weather_nws` | `ingest_ts, payload` |
| `bronze.ntd_monthly` | Raw dataset columns |
| `bronze.seed_{academic_days,venues,class_periods,football_home}` | Hand-curated CSVs |

### Silver (clean, typed, deduplicated, joined)
| Table | Key columns | Notes |
|---|---|---|
| `silver.vehicle_pings` | `vehicle_id, ts_gps` (PK), `route_short_name, direction_key, direction_name, lat, lon, speed, heading, onboard, capacity, load_factor, is_extra_trip, h3_r10, shape_dist_m, nearest_stop_id, nearest_stop_dist_m, data_origin` | Deduplicate on (vehicle, ts_gps). Expectations: inside the College Station/RELLIS bounding box; `0 ≤ onboard ≤ 1.5×capacity`; GPS age < 5 min. Speed units are unknown (values 0–17.5) — work them out from Δdistance/Δt |
| `silver.stop_visits` | `vehicle_id, route, direction, stop_id, arrive_ts, depart_ts, dwell_s, onboard_before, onboard_after, net_board_proxy, matched_trip_id, sched_ts, delay_s` | Project pings onto the GTFS shape and interpolate when the vehicle crosses the stop's along-shape distance. Match a trip by nearest scheduled time on the same route/direction/service date |
| `silver.headways` | `stop_id, route, direction, arrive_ts, prev_arrive_ts, obs_headway_s, sched_headway_s, headway_ratio, is_bunched, is_gap` | Window function `lag()` over each stop/route/direction |
| `silver.schedule_expanded` | `service_date, trip_id, route, direction_id, stop_id, stop_seq, sched_arrival_ts, is_timepoint` | GTFS × `calendar_dates` |
| `silver.events` | `event_id, title, start_ts, end_ts, is_all_day, venue_norm, lat, lon, campus, category, audience[], is_athletics, is_football_home, est_attendance, significance, is_canceled` | Keep College Station only; drop Zoom/online; drop recurring "Hours"/"Course Support"/"Group Study"; geocode via `seed_venues` (Kyle Field ≈102k, Reed Arena ≈13k, Olsen Field, Davis Diamond, Rudder Auditorium/Theatre, MSC, Rec Center …) |
| `silver.weather_hourly` | `ts_hour, temp_c, apparent_c, precip_mm, wind_kph, weather_code, source, is_forecast, forecast_issued_ts` | |
| `silver.service_alerts` | `alert_id, start_ts, end_ts, daily_window, routes[], closed_stops[], text` | Parsed from the HTML `description` |
| `silver.academic_days` | `date, term, is_class_day, is_holiday, is_break, is_reading_day, is_finals, week_of_term, days_to_finals` | |

### Gold (features, aggregates, outputs)
| Table | Grain | Contents |
|---|---|---|
| `gold.route_dir_15m` | route × direction × 15 min | `avg/p90/max load_factor, active_vehicles, sched_trips, service_ratio, headway_cv, bunching_rate, mean_delay_s, data_origin` |
| `gold.stop_15m` | stop × 15 min | `arrivals, mean_headway_ratio, net_board_proxy_sum, max_passing_load` |
| `gold.event_exposure` | route × direction × 15 min | Decayed significance of events whose venue lies within an H3 k-ring of the route's stops; pre/post-event phase |
| `gold.training_set` | route × direction × t0 | All features as of t0, plus labels `y_p90load_h{15,30,60}`, `y_crowd_h*`, `y_hwcv_h*`, `data_origin` |
| `gold.predictions` | scoring_ts × route × direction × horizon | `pred_p10/p50/p90, p_crowd, top_factors ARRAY<STRUCT<family,feature,value,contribution>>, model_uri, model_version` |
| `gold.pred_vs_actual` | Joined after the fact | Drives the historical analytics page |
| `gold.recommendations` | origin stop × destination stop × departure option | `route, depart_ts, pred_wait_min, pred_load, score, reason` |

---

## 5. Feature engineering (PySpark)

| Family | Features | Spark technique |
|---|---|---|
| Time | minute-of-day sin/cos, day of week, weekend flag, **minutes to next class start / since last class end** (class-change wave), week of term, academic flags | Join with `seed_class_periods`; `F.sequence` to build the grid |
| Recent state | load factor at t0, t0−15, −30, −60; Δload over 30 min; active vehicles ÷ scheduled (service shortfall); headway CV and bunching over the last 30 min | `Window.partitionBy(route,dir).orderBy(ts).rangeBetween` |
| Seasonal | same 15-min slot last week (when available); route × day-type × slot historical mean (also baseline #2) | Aggregates joined back |
| Events | Σ significance × time kernel for events near the route; minutes until the next big event starts / since the last one ended; football home-game day flag; venue type | H3 k-ring join (venue cell ∈ ring of route stop cells), range join on time; `significance = log1p(capacity or rsvp) × category weight × audience weight` |
| Weather | precipitation now and next hour (forecast), rain onset, heat (apparent > 35 °C), wind | As-of join on hour |
| Service | active detour/alert on the route, closed-stop count, upcoming alert within 60 min | Interval join to `silver.service_alerts` |
| Static | route length, stop count, scheduled frequency at that hour, `serves_kyle_field`, `serves_msc` | From GTFS |

**Leakage controls:**
- Features are strictly ≤ t0, and window functions never look ahead.
- Train/validation/test are split by time (forward-chaining), never randomly.
- Future weather uses forecasts as of t0. If archived forecasts aren't available in time, use observed weather and **say so in the model card**.

---

## 6. Models and MLflow

1. **Baselines (both required):**
   - B0, persistence: future load = current load.
   - B1, seasonal profile: mean load for route × day-type × 15-min slot.
2. **Main model:** one LightGBM model per horizon across all routes (route and direction as categorical features).
   - Quantile objectives (α = 0.1/0.5/0.9) give prediction intervals.
   - A binary model gives P(crowded).
3. **Explanations:**
   - LightGBM `pred_contrib=True` gives exact per-prediction SHAP values without an extra library.
   - Group them into families ("Class change at 10:20", "Kyle Field game, kickoff in 45 min", "Rain 3 mm/h", "Bunching on route 15").
   - Show the top 3 with signs.
4. **Event uplift:** score each window again with the event features neutralised. The difference is "event-attributable load" (attribution, not causation).
5. **MLflow (experiment `/Shared/aggiepulse`):**
   - Log params, Optuna nested runs, and metrics by horizon × route × `data_origin`: MAE, RMSE, pinball loss, interval coverage, PR-AUC, Brier score.
   - Log plots: residuals by hour, SHAP summary.
   - Log the training table version as an input/tag, so every run is tied to an exact Delta version.
   - Register the model in UC as `aggiepulse.ml.crowding_h{15,30,60}` with alias `@champion`. The scoring job loads it by alias.
6. **Acceptance bar for the demo:** on the observed holdout, LightGBM beats B1 on MAE at 30 min, and the p10–p90 interval covers about 80% of outcomes. If it doesn't beat the baselines, show that honestly, including where it loses.

---

## 7. Frontend (Databricks App)

| Page | Contents |
|---|---|
| **Live map** | GTFS route shapes, coloured by predicted 30-min crowding; buses as icons with a load ring (**Measured**) refreshing every 15 s; stops; active alerts and detours; event venues pulsing by significance |
| **Forecast** | Next 15/30/60 min heat strip (route × horizon); top 5 risky routes/stops; interval bands; "why" chips from SHAP families |
| **Event intelligence** | Next 7 days of significant events (filtered and scored), predicted transit impact per event (uplift), affected routes, known detours from TripSpark |
| **Plan my trip** | Origin → destination stops; options = GTFS routes serving both (plus ≤ 400 m walk to an alternative stop) × departure shifts (−10/0/+10/+20 min); score = predicted wait + ride time + crowding penalty; one-line reason |
| **History** | Predicted vs observed time series; error by hour and route; recurring hotspots (route × slot heatmap); event-day vs normal-day comparison |

**Labelling:** every number carries a provenance badge — `MEASURED · APC`, `DERIVED · GPS`, `PREDICTED`, or `SIMULATED`.

**Latency:** the app reads `silver.vehicle_latest` (refreshed every collector cycle) through the SQL warehouse, with a 15 s server-side cache. Stretch: sync gold tables to Lakebase for sub-100 ms reads.

---

## 8. Task breakdown (4 people, ~36 h)

Roles: **DE** = data engineer, **ML** = machine learning, **FE** = frontend, **INT** = integration/demo owner.

| Hours | DE | ML | FE | INT |
|---|---|---|---|---|
| **0–2** | **Collector live in a Databricks job + laptop backup**; Volume landing paths | Seed CSVs: class periods, academic days, venues + capacities | App skeleton deployed to Databricks Apps; map with GTFS shapes from a static export | UC catalog/schemas, warehouse, repo, secrets; email `<redacted-email>` |
| 2–8 | Bronze via Auto Loader; GTFS, events backfill (Aug 2026→+30 d), Open-Meteo, NWS, NTD ingest | Silver events cleaning + significance scoring; weather hourly | Live map reading `vehicle_latest`; bus icons with load ring | Pipeline expectations; lineage check; simulator spec |
| 8–16 | Silver `stop_visits` (shape projection), `headways`, `schedule_expanded`; gold 15-min aggregates | **Simulator** (flagged), calibrated to NTD + observed profile; `gold.training_set` | Forecast page with mocked `gold.predictions` schema | Scoring job skeleton; AI/BI dashboard on gold |
| 16–24 | Event exposure (H3), alerts join; data-quality fixes | Baselines B0/B1 → LightGBM quantile + classifier; MLflow; register `@champion` | Event intelligence + Plan my trip | Scoring job every 5 min writes predictions + contributions |
| 24–30 | Harden collector; backfill gaps; performance | Observed-only holdout eval; SHAP → factor families; uplift | History page; provenance badges; polish | Genie space; end-to-end run |
| 30–36 | Freeze data; snapshot tables (Delta time travel tag) | Model card (limitations, simulated vs observed) | Replay mode (time slider over collected data) | **Demo script, rehearsal ×2, backup video** |

**Checkpoints:**
- **H2:** collector writing data.
- **H8:** Bronze/Silver queryable.
- **H16:** training set exists.
- **H24:** first `@champion` predictions visible in the app.
- **H30:** feature freeze.

---

## 9. Demo strategy (5 minutes)

1. **Hook (30 s):** "TAMU moves about 1M riders a month (NTD). Here's what's happening right now." Open the live map: real buses with real APC load rings.
2. **Honesty beat (30 s):** "These occupancy numbers are measured, not guessed. Here's what's measured, what's derived, and what's simulated." Point at the badges.
3. **Databricks depth (90 s):**
   - The pipeline graph, Bronze → Silver → Gold, with expectation pass rates.
   - UC lineage from the raw Volume to `gold.predictions`.
   - MLflow comparing B0 / B1 / LightGBM on the observed holdout.
   - One Genie question ("Which routes bunched most this weekend?").
4. **Forecast + why (60 s):** a route flagged crowded in 30 min, with "Class change at 10:20 (+0.18)", "Rain starting (+0.07)".
5. **Event intelligence (60 s):** **Sat 11‑14, Tennessee at Kyle Field.**
   - The forecast impact on routes serving Kyle Field, plus the known detours already announced by TripSpark.
   - Label it as a forecast with a simulated prior, unless you collected the 11‑14 data.
   - If the demo happens after 11‑14, replay the actual game day: predicted vs observed.
6. **Student value (30 s):** Plan my trip: "Leave 10 min earlier on Route 12 instead — 40% less crowded."
- **Backups:** pre-recorded video; app "replay mode" over frozen tables (works offline from TripSpark); screenshots of MLflow.

---

## 10. Risks and mitigations

| Risk | Mitigation |
|---|---|
| TripSpark changes or blocks the endpoints, or the token scheme changes | Session refresh on any non-JSON response; 30 s cadence; the laptop backup collector; frozen-data replay mode; tell TAMU via `<redacted-email>` |
| APC accuracy unknown (sensor drift, stuck counts) | Expectations flag counts that stay flat for 60+ min while the bus moves and stops; report load as "APC-reported" |
| Thin, weekend-only observed data | Keep collecting past the hackathon; evaluate only on observed data; show sample sizes on every metric |
| Simulator assumptions leak into "findings" | Track `data_origin` end to end; observed-only metrics; "simulated prior" badge on event uplift |
| Noisy event calendar (other campuses, study sessions, 75% without coordinates) | Rules + venue lookup; show the filtered vs raw count; a manual football schedule CSV as a safety net |
| Time zones / DST (`lastGpsDate` is local without an offset) | Convert with `America/Chicago` on ingest; unit test around 2026‑11‑01 (DST end) |
| Trip matching wrong for headway-style service | Treat T3 (delay) as a stretch; T2 (headways) doesn't need trip IDs |
| robots.txt / terms | Collect only from `/RouteMap/*` (allowed); skip `/Schedule/*`; no personal data (vehicle-level only); attribute TripSpark/TAMU, Open-Meteo (CC BY 4.0), NWS, NTD |

---

## 11. Repo layout
```
aggiepulse/
  collector/            poll_tripspark.py (session+token, 30 s loop, JSONL → Volume), backup runner
  ingest/               gtfs.py, events_livewhale.py, weather_openmeteo.py, weather_nws.py, ntd.py
  pipelines/            bronze.py, silver_*.py, gold_*.py   (Lakeflow Declarative Pipeline)
  seeds/                class_periods.csv, academic_days.csv, venues.csv, football_home.csv
  sim/                  simulate_history.py (+ calibration notebook)
  ml/                   train.py, evaluate.py, score.py, explain.py, model_card.md
  app/                  backend/ (FastAPI), frontend/ (React + MapLibre), app.yaml
  databricks.yml        Asset Bundle: jobs, pipeline, app, warehouse refs
```
Deploy everything with **Databricks Asset Bundles** (`databricks bundle deploy`) so the whole system can be reproduced from the repo. That is worth calling out to judges.

---

## 12. Later: public copy of the site (deferred 2026-11-07)

**Now:** the Databricks App requires a workspace login, so viewers are added by hand (see README → "Giving someone access").

**Later:** host a public, read-only copy outside Databricks.
- **Host:** the same FastAPI + React app (`aggiepulse/app/`) as a container on Cloud Run, Fly.io or Render. The backend already supports `AGGIEPULSE_BACKEND=databricks` with env-based auth.
- **Auth to Databricks:** a dedicated service principal with an OAuth secret (`DATABRICKS_HOST/CLIENT_ID/CLIENT_SECRET` stored as host secrets). Grants: CAN_USE on warehouse `3c5b1dbf21774cde`; SELECT on only the tables the app reads (`silver.vehicle_latest`, `silver.events`, `silver.service_alerts`, `gold.predictions_latest`, `gold.route_dir_15m`); READ VOLUME on `transit.raw.live`. No write access.
- **Cost control:** public traffic keeps the serverless warehouse awake. Raise the server cache to about 60 s for non-vehicle data and add per-IP rate limiting. Alternatively, publish a JSON snapshot every 1–5 min to a CDN or object storage and serve the site fully statically, so the warehouse never sees public traffic. That is the cheaper option and is preferred.
- **Before going public:** keep the provenance labels and caveats visible; add attribution (TAMU Transportation Services/TripSpark, Open-Meteo CC BY 4.0, NWS, NTD); tell TAMU (`<redacted-email>`) first, since the site republishes TripSpark data; check that no personal data is exposed (vehicle-level only).
