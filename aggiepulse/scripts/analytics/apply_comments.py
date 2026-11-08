"""Apply deterministic UC documentation, skipping unchanged comments on reruns.

Pipeline-managed/view column ALTER failures are captured in comments_report.json;
build_assets.py includes their complete documentation in the Genie space instead.
No pipeline definitions or ownership are changed. Run with uv run python .../apply_comments.py.
"""

from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from client import SqlClient, quote_identifier, quote_literal
from databricks.sdk.errors import DatabricksError

ROOT = Path(__file__).resolve().parents[2]

TABLES = {
    "apc_quality": "MEASURED · APC / DERIVED · GPS: per vehicle/local service-day sensor quality counts, including quarantined over-capacity candidates and time with suspicious flat APC while moving between stops.",
    "ops_health": "DERIVED: scheduled operations watchdog checks, collector/pipeline/scoring freshness and daily APC quality totals; operational status, not passenger demand.",
    "vehicle_pings": "MEASURED · APC / DERIVED · GPS: deduplicated TripSpark vehicle/GPS readings; one vehicle_id and ts_gps. APC accuracy is vendor-unverified.",
    "vehicle_latest": "MEASURED · APC / DERIVED · GPS: latest reading per vehicle; apply a five-minute ts_gps freshness filter before calling a bus active.",
    "vehicle_pings_candidates": "MEASURED · APC / DERIVED · GPS: normalized TripSpark readings before quality filtering; may contain invalid readings.",
    "vehicle_pings_quarantine": "MEASURED · APC / DERIVED · GPS: rejected candidate readings with failed_expectations; do not use as valid occupancy observations.",
    "tripspark_routes": "DERIVED · GPS: TripSpark route/direction metadata joined to official TAMU GTFS route identities.",
    "tripspark_route_mismatches": "DERIVED · GPS: diagnostic TripSpark route identities that could not be matched to GTFS.",
    "schedule_expanded": "DERIVED · GPS: official TAMU GTFS stop schedule expanded by service_date and calendar exceptions; scheduled times are not observed arrivals.",
    "route_direction_map": "DERIVED · GPS: ranked GPS-to-GTFS shape/direction matching candidates; only is_selected=true is the chosen mapping.",
    "stop_visits": "DERIVED · GPS: inferred stop arrivals/departures from GPS shape projection, about ±15 seconds at 30-second polling; net_board_proxy is net APC change, not gross boardings.",
    "headways": "DERIVED · GPS: consecutive inferred arrivals at a stop/route/direction, compared with official GTFS schedule; bunching ratio <0.5, gaps >1.5.",
    "events": "DERIVED · GPS: curated TAMU LiveWhale College Station event calendar; virtual/low-relevance/canceled events filtered, coordinates may use seeded venue lookup. Event significance and attendance are assumptions, not measured demand.",
    "events_evaluated": "DERIVED · GPS: TAMU LiveWhale event candidates with filter reason before final retention; audit dataset, not the curated event list.",
    "events_filter_stats": "DERIVED · GPS: counts of LiveWhale event candidates raw/kept/dropped grouped by filter reason; sums reconstruct the evaluated event set.",
    "service_alerts": "DERIVED · GPS: TripSpark current/upcoming service interruptions; dates UTC, recurring daily windows America/Chicago, affected route and closed stop lists.",
    "weather_hourly": "DERIVED · GPS: hourly Open-Meteo/NWS weather context; consult source, is_forecast and forecast_issued_ts. Forecasts and observations are different inputs.",
    "academic_days": "DERIVED · GPS: hand-curated TAMU academic-calendar seed, one College Station local calendar date; class/holiday/finals features.",
    "class_periods": "DERIVED · GPS: seeded registrar class schedule grid; local America/Chicago clock times and source/retrieval metadata.",
    "ntd_monthly": "MEASURED: US FTA National Transit Database TAMU agency 60278 monthly totals; UPT is unlinked passenger trips (boardings), not distinct riders or APC onboard counts.",
    "route_dir_15m": "MEASURED · APC / DERIVED · GPS: route/direction/UTC 15-minute aggregate of valid vehicle pings and inferred service metrics; n_pings is sample count.",
    "route_dir_15m_sim": "SIMULATED: synthetic route/direction/15-minute Fall 2026 history from GTFS, weather, events and seeded class waves, calibrated to NTD. Assumed event effects are not discoveries.",
    "stop_15m": "DERIVED · GPS: stop/UTC 15-minute inferred arrivals, headway and net APC boarding proxy; not measured gross boardings.",
    "event_exposure": "DERIVED · GPS / SIMULATED prior: route/direction/15-minute event proximity and phase weights from calendar events and route stops (600 m radius); significance is assumed, not causal impact.",
    "training_set": "MEASURED · APC / SIMULATED: feature snapshots at route/direction/t0 with future-window labels, temporal splits and explicit origin; observed labels holdout-only. Excluded from Genie.",
    "seasonal_profile": "SIMULATED prior: training-only as-of route/direction/day-type/quarter-hour seasonal load baseline; available_at prevents looking ahead. Not observed performance evidence.",
    "predictions": "PREDICTED: scoring_ts/route/direction/horizon crowding predictions from UC crowding_h15/h30/h60 champion aliases. Target is future 15-minute p90 APC load. Training origin does not imply actual target origin.",
    "predictions_latest": "PREDICTED: latest scoring snapshot per route/direction/horizon; pred_p10/p50/p90 are quantiles of future 15-minute p90 load factor, not passenger counts.",
    "pred_vs_actual": "PREDICTED vs MEASURED · APC / SIMULATED: predictions joined to future target-window actual aggregates. Separate actual_data_origin; deduplicate latest scoring_ts per t0/route/direction/horizon/actual_data_origin for metrics, exclude unmatched actuals. Report n.",
}

COLUMNS = {
    "status": "Operations health result OK/FAIL/ERROR; FAIL means collector silent >30 min while GTFS service expected.",
    "run_id": "Unique watchdog health-check run identifier.",
    "service_expected": "Whether current UTC falls within any route-day GTFS first-to-last scheduled arrival envelope, including internal schedule gaps.",
    "collector_age_min": "Minutes since most recent HTTP 200 collector poll with nonnull payload; polling can be healthy even with no active buses.",
    "pipeline_lag_min": "Nonnegative minutes between last successful Bronze collector receipt and latest Silver ping ingest; NULL if Silver empty.",
    "scoring_age_min": "Minutes since latest prediction scoring timestamp.",
    "apc_pings": "Candidate pings for current America/Chicago service date, one vehicle/GPS timestamp.",
    "pings": "Count of deduplicated vehicle/GPS-time candidate readings per local service day, before quality rejection.",
    "apc_over_capacity_pings": "Daily readings with onboard > capacity, including quarantined candidates; count, not distinct passengers.",
    "over_capacity_pings": "Candidate readings with onboard > capacity for vehicle/day, including rejected readings >1.5x capacity.",
    "apc_stuck_minutes": "Minutes of suspicious unchanged APC while GPS movement and distinct stop proximity indicate travel; for pings this is incremental elapsed contribution, for health rows daily total.",
    "stuck_minutes": "Sum of incremental suspicious flat-APC duration per vehicle/local service day, minutes; diagnostic, not proven sensor fault.",
    "apc_flat_minutes": "Elapsed minutes in contiguous same route/onboard/capacity run, resetting after long GPS gap; not alone evidence of sensor failure.",
    "apc_stuck": "Diagnostic flag for flat APC >=60 minutes with GPS movement and multiple nearby stops; vendor accuracy remains unverified.",
    "apc_over_capacity": "Reported onboard exceeds capacity; crowded can occur below this threshold.",
    "error_message": "Watchdog failure detail from missing/unqueryable upstream tables; NULL for normal health checks.",
    "route_short_name": "Official route string, retain leading zeros and hyphens: 01, 01-04, 03-05, 40-41. Hyphenated names are distinct combined night/weekend services, not numeric ranges.",
    "route_id": "Official GTFS route identifier; join gtfs_routes for display name.",
    "direction_id": "GTFS direction integer 0/1; use direction_name for display, do not assume inbound/outbound semantics.",
    "direction_key": "TripSpark direction GUID; map through selected route_direction_map, not directly to GTFS direction_id.",
    "route_key": "TripSpark route GUID, distinct from official GTFS route_id.",
    "onboard": "MEASURED · APC: reported people currently onboard; vendor counter accuracy unverified.",
    "capacity": "Reported passenger capacity in people (typically 85; RELLIS shuttle 25); denominator for load_factor.",
    "load_factor": "MEASURED · APC: onboard / capacity, dimensionless fraction (0.75 = 75% full); can exceed 1.0. Crowded means >=0.75.",
    "speed": "TripSpark vendor speed value; units unverified. Do not label mph or km/h.",
    "heading": "GPS compass bearing in degrees clockwise from north.",
    "data_origin": "Provenance: 'observed' or 'simulated'. Never combine simulated and observed metrics unless explicitly asked; preserve this field in groups and joins.",
    "actual_data_origin": "Actual target provenance ('observed' or 'simulated'); use this to separate evaluation lines. NULL means no matched actual; exclude from metrics.",
    "training_data_origin": "Training provenance (often simulated), NOT the origin of matched evaluation actuals; use actual_data_origin for observed performance.",
    "horizon_min": "Forecast lead time in minutes: 15, 30 or 60. Target window is [t0+horizon_min, t0+horizon_min+15 minutes).",
    "pred_p10": "PREDICTED: 10th quantile of target-window p90 load_factor; fraction, lower bound of nominal 80% interval.",
    "pred_p50": "PREDICTED: median of target-window p90 load_factor, fraction. Compare to actual_p90_load for MAE.",
    "pred_p90": "PREDICTED: 90th quantile of target-window p90 load_factor; fraction, upper bound of nominal 80% interval.",
    "p_crowd": "PREDICTED: probability in [0,1] that target-window maximum load_factor >=0.75.",
    "actual_p90_load": "MEASURED · APC if actual_data_origin=observed, otherwise SIMULATED: target-window 90th percentile load_factor, fraction.",
    "actual_max_load": "Target-window maximum load_factor, fraction; consult actual_data_origin.",
    "actual_crowd": "Binary target: actual_max_load >=0.75 (1 crowded, 0 otherwise); consult actual_data_origin.",
    "error_p50": "Signed prediction residual actual_p90_load - pred_p50 in load-factor fraction units; abs for MAE.",
    "actual_in_p10_p90": "Whether actual_p90_load lies inclusively between pred_p10 and pred_p90; average cast double for nominal 80% interval coverage.",
    "actual_n_pings": "Number of target-window source pings; not the number of independent forecast examples.",
    "model_name": "Fully qualified UC model name transit.ml.crowding_h{horizon}; champion alias resolved when scoring.",
    "model_version": "UC model version actually used for this prediction; may differ from current champion after promotion.",
    "model_uri": "Registered UC model URI used to score the row.",
    "event_uplift": "PREDICTED / SIMULATED prior: predicted load difference with event features versus neutralized event features; attribution, not causal effect.",
    "top_factors": "PREDICTED: SHAP factors (family,feature,value,contribution) grouped for display; contribution in predicted load-factor units.",
    "avg_load_factor": "Mean onboard/capacity across source pings in the 15-minute window; fraction. Consult data_origin.",
    "p90_load_factor": "90th percentile onboard/capacity across source pings in the 15-minute window; fraction. Consult data_origin.",
    "max_load_factor": "Maximum onboard/capacity across source pings in the 15-minute window; fraction. Consult data_origin.",
    "obs_headway_s": "DERIVED · GPS: seconds between successive inferred arrivals at this stop/route/direction; provenance separated.",
    "sched_headway_s": "Seconds between adjacent scheduled GTFS arrivals at the matched stop/route/direction.",
    "headway_ratio": "obs_headway_s / sched_headway_s, dimensionless; NULL if no valid schedule match.",
    "is_bunched": "True when headway_ratio <0.5; NULL if no valid headway/schedule denominator.",
    "is_gap": "True when headway_ratio >1.5; NULL if no valid headway/schedule denominator.",
    "headway_cv": "DERIVED · GPS: sample standard deviation / mean of observed headways, dimensionless; undefined for insufficient headways. SIMULATED when data_origin=simulated.",
    "bunching_rate": "Fraction of valid matched headways with ratio <0.5 in window, [0,1]; consult data_origin.",
    "mean_headway_ratio": "Mean valid observed/scheduled headway ratio at stop/window; dimensionless.",
    "net_board_proxy": "onboard_after - onboard_before in people inferred across stop visit; net change, NOT gross boardings.",
    "net_board_proxy_sum": "Sum of net onboard changes across stop visits in window, people; NOT gross boardings.",
    "onboard_before": "APC-reported onboard people before inferred stop visit.",
    "onboard_after": "APC-reported onboard people after inferred stop visit.",
    "dwell_s": "Inferred stop dwell duration depart_ts-arrive_ts in seconds.",
    "delay_s": "Inferred arrival minus matched scheduled arrival in seconds; positive late; heuristic trip match.",
    "mean_delay_s": "Mean inferred arrival delay in seconds; positive late; heuristic trip matching.",
    "arrivals": "Count of inferred stop arrivals in UTC 15-minute window, not passenger boardings.",
    "active_vehicles": "Distinct vehicles in window, not instantaneous fleet size; SIMULATED on synthetic rows.",
    "sched_trips": "Distinct scheduled GTFS trips in the UTC 15-minute window.",
    "service_ratio": "Active vehicles / scheduled trips in window; dimensionless availability proxy, not on-time performance.",
    "max_passing_load": "Maximum APC-reported onboard/capacity of inferred passing visits; fraction, consult data_origin.",
    "n_pings": "Count of source GPS/APC pings; repeated readings from one bus are not independent riders.",
    "event_exposure": "Assumed event significance × spatial/temporal proximity weight summed near route stops (600 m); dimensionless prior, not causal or measured demand.",
    "event_pre": "Summed assumed pre-event significance/time-kernel weight, dimensionless.",
    "event_post": "Summed assumed post-event significance/time-kernel weight, dimensionless.",
    "event_during": "Summed assumed during-event significance/time-kernel weight, dimensionless.",
    "event_timing_is_assumed": "Indicator: some event timing filled from seeded assumptions; disclose when interpreting exposure.",
    "event_schedule_is_hindsight": "Indicator: historical calendar availability was not archived as-of t0; potential hindsight context.",
    "unknown_size_events": "Count of nearby events with unknown significance/attendance, not zero-demand events.",
    "event_geo_method": "Spatial matching method used for exposure; implementation uses route-stop proximity, not a causal catchment.",
    "significance": "Heuristic dimensionless log1p(attendance/capacity) × category/audience weights; assumed importance, not measured demand.",
    "est_attendance": "Assumed event attendance in people from venue capacity or RSVP; not measured turnout.",
    "rsvp_total": "Calendar-reported RSVP count, not measured attendance or transit boardings.",
    "venue_key": "Normalized seeded venue lookup key, e.g. kyle_field.",
    "is_football_home": "Whether calendar/seed identifies a TAMU home football event.",
    "raw_count": "Number of evaluated event candidates in this filter reason group.",
    "kept_count": "Candidates retained by event relevance filter in this reason group.",
    "dropped_count": "Candidates rejected by event relevance filter in this reason group.",
    "upt": "MEASURED: NTD unlinked passenger trips (boardings) during reporting month; not unique riders or onboard occupancy.",
    "vrm": "NTD vehicle revenue miles during reporting month, miles.",
    "vrh": "NTD vehicle revenue hours during reporting month, hours.",
    "voms": "NTD vehicles operated in maximum service, count.",
    "month": "First calendar date of NTD reporting month; not a UTC event timestamp.",
    "mode": "NTD transit mode code, MB is motor bus.",
    "tos": "NTD type-of-service code, DO is directly operated.",
    "ntd_id": "FTA National Transit Database agency identifier; TAMU 60278.",
    "temp_c": "Air temperature in degrees Celsius.",
    "apparent_c": "Apparent (feels-like) temperature in degrees Celsius.",
    "precip_mm": "Hourly precipitation in millimeters.",
    "rain_mm": "Hourly rain in millimeters.",
    "wind_kph": "Wind speed in kilometers/hour.",
    "weather_code": "Provider weather condition code; interpret with source metadata.",
    "is_forecast": "True for provider-issued forecast weather; false for historical/observed weather context.",
    "feature_staleness_min": "Minutes from feature reference time t0 to scoring_ts; older snapshots reduce live relevance.",
    "split": "Forward-time train/validation/test assignment; observed labels reserved for holdout; no random split.",
    "seasonal_slot_mean": "As-of train-only mean load_factor by route/direction/day-type/15-minute slot; synthetic prior, fraction.",
    "slot": "Quarter-hour local-day slot 0..95 derived in America/Chicago; may repeat across DST fold.",
    "minute_of_day": "Local America/Chicago clock minute since midnight, 0..1439.",
    "dow": "Local day-of-week index, Monday=0 through Sunday=6.",
    "day_type": "Local calendar grouping such as weekday/weekend for seasonal baseline.",
    "pattern": "Registrar class meeting weekday pattern, e.g. MWF or TR.",
    "start_time": "Class start as America/Chicago local HH:MM clock time.",
    "end_time": "Class end as America/Chicago local HH:MM clock time.",
    "start_time_seconds": "Class start in local seconds since midnight, not Unix epoch.",
    "end_time_seconds": "Class end in local seconds since midnight, not Unix epoch.",
    "arrival_time": "Official GTFS local service-day HH:MM:SS; can exceed 24:00.",
    "departure_time": "Official GTFS local service-day HH:MM:SS; can exceed 24:00.",
    "shape_dist_traveled": "GTFS publisher-provided along-shape distance; vendor units not guaranteed, do not assume meters.",
    "fit_quality": "GPS-to-shape heuristic exp(-median_offset_m/50), dimensionless [0,1], higher better.",
    "backward_fraction": "Fraction of GPS projected steps moving backwards on candidate shape.",
    "candidate_rank": "Candidate direction/shape fit rank, 1 best.",
    "is_selected": "Selected best candidate (rank 1 and median offset <=100 m).",
    "failed_expectations": "Names of quality expectations failed by quarantined reading.",
    "_normalization_revision": "Normalization code/version marker for deterministic ingestion refreshes.",
    "agency": "NTD reporting agency name, Texas A&M University Transportation Services.",
    "agency_id": "Official GTFS agency identifier.",
    "alert_id": "TripSpark service-interruption identifier.",
    "audience": "LiveWhale intended audience labels; used in heuristic significance weighting.",
    "block_id": "GTFS vehicle work-block identifier linking sequential scheduled trips; not observed vehicle_id.",
    "campus": "Calendar campus classification retained by the College Station relevance filter.",
    "campus_region": "Registrar class-grid campus/region applicability.",
    "category": "Calendar event category used in significance weighting.",
    "color": "TripSpark route display color, hexadecimal RGB.",
    "destination": "TripSpark direction destination/headsign text.",
    "details": "Lakeflow event-specific JSON, including flow_progress.metrics and data_quality expectations/dropped_records.",
    "direction_name": "TripSpark human-readable direction/headsign, not necessarily inbound/outbound.",
    "drop_off_type": "GTFS drop-off rule: 0 regular, 1 unavailable, 2 phone arrangement, 3 driver arrangement.",
    "pickup_type": "GTFS pickup rule: 0 regular, 1 unavailable, 2 phone arrangement, 3 driver arrangement.",
    "error": "Lakeflow error structure, including exception text and fatal/retry context; NULL for normal events.",
    "event_id": "TAMU LiveWhale calendar event identifier; distinct from pipeline event-log id.",
    "event_type": "Lakeflow event type such as flow_progress; select completed flow records for expectation metrics.",
    "event_types": "Original LiveWhale event-type labels retained for calendar classification.",
    "group_title": "LiveWhale calendar publishing/organizing group.",
    "id": "Unique Lakeflow pipeline event-log record identifier.",
    "level": "Lakeflow log severity, e.g. INFO, WARN or ERROR.",
    "location_title": "Calendar venue display text; seeded venue_key/coordinates may normalize variants.",
    "location_type": "GTFS location type: 0 stop/platform, 1 station, 2 entrance, 3 generic node, 4 boarding area.",
    "matched_trip_id": "Heuristically matched GTFS trip for stop visit; NULL if no confident schedule match.",
    "maturity_level": "Lakeflow event schema maturity, e.g. STABLE or EVOLVING.",
    "message": "Human-readable Lakeflow event log message; structured metrics are in details.",
    "nearest_stop_id": "Nearest official GTFS stop identifier to GPS fix; proximity alone is not an arrival.",
    "notes": "Academic seed annotation or source caveat, not a measured feature.",
    "origin": "Lakeflow pipeline/flow/update/batch identity struct; group by pipeline_id and flow_name to scope metrics.",
    "patterns": "TripSpark route direction pattern metadata; associated geometry comes from GTFS/TripSpark paths.",
    "poll_id": "Collector poll identifier; multiple bus readings share one poll.",
    "reason": "Normalization/filter diagnostic reason; retained/rejected classification explained by kept/dropped counts.",
    "route_color": "Official GTFS route background hexadecimal RGB color without #.",
    "route_desc": "Official GTFS route description text.",
    "route_long_name": "Official GTFS human-readable route name, e.g. Bonfire.",
    "route_name": "TripSpark human-readable route display name.",
    "route_text_color": "Official GTFS route foreground/text hexadecimal RGB color without #.",
    "route_type": "Official GTFS transport mode code; 3 is bus (extended codes follow publisher specification).",
    "sequence": "Lakeflow event ordering structure; use event timestamp and origin for reporting scopes.",
    "service_id": "GTFS service-calendar identifier, joined to calendar_dates to expand operating dates.",
    "service_interruption_keys": "TripSpark identifiers of currently active service interruptions.",
    "shape_id": "Official GTFS route-path geometry identifier, join gtfs_shapes within feed_version.",
    "stop_code": "Public TAMU stop code; matches TripSpark stop metadata.",
    "stop_desc": "Official GTFS stop description.",
    "stop_headsign": "GTFS destination/headsign override at this trip stop.",
    "stop_id": "Official GTFS stop identifier, join gtfs_stops for name/coordinates.",
    "stop_name": "Official TAMU GTFS human-readable stop name.",
    "term": "Seeded TAMU academic term label, e.g. Fall 2026.",
    "text": "TripSpark interruption advisory text, parsed from original HTML description.",
    "title": "Source calendar event or TripSpark alert human-readable title.",
    "trip_headsign": "Official GTFS scheduled trip destination/headsign.",
    "trip_id": "Official GTFS scheduled trip identifier within feed_version; not an observed vehicle identifier.",
    "upcoming_interruption_keys": "TripSpark identifiers of announced future service interruptions.",
    "vehicle_id": "TripSpark vehicle identifier; one bus can contribute many time-series readings.",
    "vehicle_name": "TripSpark fleet display number/name, not a passenger identifier.",
    "venue_type": "Seeded event venue category used for demand-context features.",
    "wheelchair_boarding": "GTFS accessibility: 0 unknown/inherited, 1 accessible boarding, 2 inaccessible boarding.",
    "_source_file": "Immutable landing file path used for ingestion lineage.",
    "_rescued_data": "Auto Loader unparsed/unexpected fields serialized as JSON; NULL is normal.",
    "feed_version": "Official TAMU GTFS feed version string; join same versions when present.",
    "source": "Original data provider or seed source; consult alongside provenance flags.",
    "source_host": "TripSpark source hostname for collection attribution.",
    "daily_window": "Service alert recurring local clock window; evaluate with America/Chicago, not UTC clock alone.",
    "routes": "Affected official route_short_name strings, including hyphenated weekend services.",
    "closed_stops": "Affected stop identifiers from TripSpark service interruption metadata.",
}


def table_comment(schema: str, table: str) -> str:
    if table.startswith("gtfs_"):
        desc = f"DERIVED · GPS: official TAMU Transportation Services GTFS {table[5:]} schedule/reference feed, typed from Bronze; schedule is not measured service."
    elif table.startswith("event_log_"):
        desc = "DERIVED: native Lakeflow pipeline event log; operational lineage/expectation evidence, not passenger observations. Sum expectation passes/failures separately from dropped_records to avoid double counting."
    else:
        desc = TABLES.get(
            table,
            f"DERIVED: {schema}.{table} is an additional curated analytics table discovered in live UC metadata; inspect source and provenance before treating any values as measured passengers.",
        )
    return f"{desc} All TIMESTAMP columns are UTC; convert display with from_utc_timestamp(ts, 'America/Chicago'). Preserve observed vs simulated provenance."


def column_comment(name: str, dtype: str) -> str:
    if name in COLUMNS:
        return COLUMNS[name]
    if dtype == "TIMESTAMP":
        meanings = {
            "ts_gps": "Vehicle GPS fix time (TripSpark local clock converted on ingest)",
            "ingest_ts": "Landing/ingestion receipt time",
            "t0": "Feature/reference time for forecasting",
            "scoring_ts": "Model scoring run time",
            "target_window_start": "Inclusive forecast verification window start t0+horizon",
            "window_start": "Inclusive UTC 15-minute aggregation window start",
            "available_at": "Earliest time this train-only seasonal profile may be used",
            "forecast_issued_ts": "Weather forecast publication/as-of time; select only issues <=t0",
            "prev_arrive_ts": "Previous inferred stop arrival for same route/direction/stop",
            "arrive_ts": "GPS-inferred stop arrival",
            "depart_ts": "GPS-inferred stop departure",
            "sched_ts": "Heuristically matched GTFS scheduled arrival",
        }
        return (
            meanings.get(name, name.replace("_", " ").capitalize())
            + "; UTC TIMESTAMP, not local clock. Convert with from_utc_timestamp(..., 'America/Chicago')."
        )
    if name.startswith("data_origin_h"):
        return (
            "Future label provenance for horizon "
            + name.split("_h")[-1]
            + " minutes; never equate with current feature origin."
        )
    if name.startswith("y_"):
        return (
            "Future 15-minute target label: "
            + name[2:]
            + "; p90load is load-factor fraction, crowd is max load>=0.75, hwcv is dimensionless headway CV. Horizon suffix is minutes; use corresponding data_origin_h*."
        )
    if name.startswith("sim_"):
        return (
            "SIMULATED: "
            + name[4:].replace("_", " ")
            + " component in synthetic load-factor units; simulator assumption, not measured effect."
        )
    if re.search(r"_lag(15|30|60)$", name):
        return (
            "Past "
            + re.sub(r"_lag", " at minus ", name)
            + " minutes relative to the last complete feature window (load_t0 starts t0-15min); load is fraction, headway CV/rates/ratios dimensionless, active vehicles count; provenance follows data_origin."
        )
    if name.startswith("minutes_") or name.endswith("_min"):
        return (
            name.replace("_", " ").capitalize()
            + "; minutes relative to feature reference time t0."
        )
    if name in {"lat", "stop_lat", "shape_pt_lat"}:
        return "Latitude WGS84 decimal degrees north; GPS/venue/official GTFS coordinate as applicable."
    if name in {"lon", "stop_lon", "shape_pt_lon"}:
        return "Longitude WGS84 decimal degrees east (negative west); GPS/venue/GTFS coordinate as applicable."
    if name.endswith("_m"):
        return (
            name.replace("_", " ").capitalize()
            + "; meters, derived from GPS/GTFS geometry."
        )
    if name in {
        "arrival_secs",
        "departure_secs",
        "arrival_time_seconds",
        "departure_time_seconds",
    }:
        return (
            "GTFS local service-day elapsed seconds; may exceed 86400; not UTC epoch."
        )
    if name.startswith("is_") or dtype == "BOOLEAN":
        return (
            name.replace("_", " ").capitalize()
            + "; boolean indicator from source calendar/metadata or derived rule; NULL means unknown."
        )
    if name in {"date", "service_date", "retrieved_date"}:
        return (
            name.replace("_", " ").capitalize()
            + "; local calendar date in America/Chicago, not a UTC instant."
        )
    extra = {
        "h3_r10": "H3 resolution-10 cell index derived from vehicle longitude/latitude.",
        "load_t0": "Mean load factor from last complete [t0-15min,t0) window, fraction; use data_origin.",
        "p90_load_t0": "P90 load factor from last complete [t0-15min,t0) window, fraction.",
        "load_same_slot_last_week": "Load-factor fraction at same local slot one week earlier; provenance-aware as-of lookup.",
        "delta_load_30m": "Current minus 30-minute-lag load-factor fraction.",
        "precip_now": "Precipitation at t0 in mm/hour, provider forecast/observation flags apply.",
        "precip_next_hour": "Next-hour precipitation in mm/hour; observed substitution flagged weather_is_observed_proxy.",
        "rain_onset": "Indicator: no precipitation now and positive next-hour precipitation.",
        "heat_gt_35c": "Indicator: apparent temperature >35 degrees Celsius.",
        "weather_is_observed_proxy": "Next-hour weather substituted from observed history when as-of forecasts unavailable; hindsight limitation.",
        "weather_now_is_observed_proxy": "Current-hour weather from observation rather than an as-of forecast.",
        "scheduled_frequency_per_hour": "Scheduled trips in t0 quarter-hour times four, trips/hour proxy.",
        "scheduled_trips_t0": "Count of GTFS scheduled trips in reference quarter-hour.",
        "stop_count": "Distinct GTFS stops served by route/direction.",
        "active_alert": "Indicator of a TripSpark interruption active at t0.",
        "closed_stop_count": "Count of stops closed by active interruption.",
        "upcoming_alert_60m": "Indicator of interruption beginning within 60 minutes after t0.",
        "service_shortfall": "Service shortfall max(1-service_ratio, 0), dimensionless.",
        "headway_cv_last30m": "Mean headway CV over past 30 minutes, dimensionless.",
        "bunching_last30m": "Mean bunching rate over past 30 minutes, fraction.",
        "football_home_game_day": "Indicator for seeded/calendar home football local date; simulated demand prior.",
        "minute_of_day_sin": "Sine of cyclic local minute-of-day, dimensionless.",
        "minute_of_day_cos": "Cosine of cyclic local minute-of-day, dimensionless.",
        "weekend": "Indicator for local Saturday/Sunday.",
        "week_of_term": "Week index since academic term start, weeks.",
        "days_to_finals": "Calendar days until finals start.",
        "seasonal_slot_mean": "Train-only as-of seasonal average load-factor fraction.",
        "stop_sequence": "GTFS order of stop within trip, ascending integer.",
        "shape_pt_sequence": "GTFS order of point within shape, ascending integer.",
        "timepoint": "GTFS 1 exact timepoint or 0 estimated time.",
        "exception_type": "GTFS calendar exception 1 added service or 2 removed service.",
    }
    return extra.get(
        name,
        name.replace("_", " ").capitalize()
        + f"; {dtype} source/derived metadata. Interpret using this table provenance and source identifiers.",
    )


def collect_metadata(client: SqlClient) -> tuple[list[dict], list[dict]]:
    where = "table_schema IN ('silver','gold') AND substring(table_name,1,2) <> '__'"
    return (
        client.rows(
            "SELECT table_schema,table_name,comment FROM transit.information_schema.tables WHERE "
            + where
        ),
        client.rows(
            "SELECT table_schema,table_name,column_name,data_type,comment FROM transit.information_schema.columns WHERE "
            + where
            + " ORDER BY table_schema,table_name,ordinal_position"
        ),
    )


def apply_comments(
    client: SqlClient, tables: list[dict], columns: list[dict], workers: int = 6
) -> dict:
    """Compare desired/actual comments, apply only differences; client is mockable."""

    def apply(table: dict) -> dict:
        schema, name = table["table_schema"], table["table_name"]
        full = f"transit.{schema}.{name}"
        quoted = ".".join(map(quote_identifier, ("transit", schema, name)))
        desired = table_comment(schema, name)
        out = {
            "table": full,
            "table_comment": desired,
            "table_status": "unchanged",
            "columns": [],
        }
        if table.get("comment") != desired:
            try:
                client.execute(f"COMMENT ON TABLE {quoted} IS {quote_literal(desired)}")
                out["table_status"] = "applied"
            except (RuntimeError, DatabricksError) as exc:
                out.update(table_status="fallback", table_error=str(exc))
        unsupported = None
        for col in [
            c
            for c in columns
            if c["table_schema"] == schema and c["table_name"] == name
        ]:
            desired_col = column_comment(col["column_name"], col["data_type"])
            entry = {
                "column": col["column_name"],
                "comment": desired_col,
                "status": "unchanged",
            }
            if col.get("comment") != desired_col:
                if unsupported:
                    entry.update(status="fallback", error=unsupported)
                else:
                    try:
                        client.execute(
                            f"ALTER TABLE {quoted} ALTER COLUMN {quote_identifier(col['column_name'])} COMMENT {quote_literal(desired_col)}"
                        )
                        entry["status"] = "applied"
                    except (RuntimeError, DatabricksError) as exc:
                        error = str(exc)
                        entry.update(status="fallback", error=error)
                        if any(
                            code in error
                            for code in (
                                "EXPECT_TABLE_NOT_VIEW",
                                "MATERIALIZED_VIEW",
                                "STREAMING_TABLE",
                                "PIPELINE",
                                "NOT_SUPPORTED",
                            )
                        ):
                            unsupported = error
            out["columns"].append(entry)
        return out

    with ThreadPoolExecutor(max_workers=workers) as pool:
        result = list(pool.map(apply, tables))
    return {
        "tables": result,
        "summary": {
            "tables": len(result),
            "table_comments_applied": sum(
                t["table_status"] == "applied" for t in result
            ),
            "columns": sum(len(t["columns"]) for t in result),
            "column_comments_applied": sum(
                c["status"] == "applied" for t in result for c in t["columns"]
            ),
            "column_fallbacks": sum(
                c["status"] == "fallback" for t in result for c in t["columns"]
            ),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="transit")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "dashboards/comments_report.json"
    )
    args = parser.parse_args()
    from databricks.sdk import WorkspaceClient

    client = SqlClient(WorkspaceClient(profile=args.profile))
    tables, columns = collect_metadata(client)
    report = apply_comments(client, tables, columns)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"]))


if __name__ == "__main__":
    main()
