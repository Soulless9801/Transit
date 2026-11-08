"""Rebuild dashboard and curated Genie definitions from live UC metadata.

Run apply_comments.py first. Generated queries are checked by validate.py before
bundle deployment. Only analytics-owned files are written.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from apply_comments import ROOT, collect_metadata, column_comment, table_comment
from client import SqlClient

DASHBOARDS = ROOT / "dashboards"
DEDUP = """WITH verified AS (
 SELECT *, row_number() OVER (
   PARTITION BY t0,route_short_name,direction_id,horizon_min,actual_data_origin
   ORDER BY scoring_ts DESC) AS rn
 FROM transit.gold.pred_vs_actual
 WHERE actual_p90_load IS NOT NULL AND actual_data_origin IN ('observed','simulated')
), latest AS (SELECT * FROM verified WHERE rn=1)
"""
BUNCH_QUESTION = "Which routes bunched most this weekend?"
MAE_QUESTION = "What is the model's 30-minute MAE on observed data?"
EVENT_QUESTION = "Which events next week are near Kyle Field?"
EXAMPLES = [
    (
        BUNCH_QUESTION,
        """SELECT route_short_name, COUNT(*) AS n_valid_headways,
 AVG(CAST(is_bunched AS DOUBLE)) AS bunching_rate,
 AVG(CAST(is_gap AS DOUBLE)) AS gap_rate
FROM transit.silver.headways
WHERE data_origin='observed' AND headway_ratio IS NOT NULL
 AND to_date(from_utc_timestamp(arrive_ts,'America/Chicago')) >=
 date_add(to_date(date_trunc('week',from_utc_timestamp(current_timestamp(),'America/Chicago'))),5)
 AND to_date(from_utc_timestamp(arrive_ts,'America/Chicago')) <
 date_add(to_date(date_trunc('week',from_utc_timestamp(current_timestamp(),'America/Chicago'))),7)
GROUP BY route_short_name ORDER BY bunching_rate DESC,n_valid_headways DESC""",
    ),
    (
        MAE_QUESTION,
        DEDUP
        + """SELECT CASE WHEN grouping(route_short_name)=1 THEN 'ALL ROUTES' ELSE route_short_name END AS route_short_name,
 model_name, model_version, COUNT(*) AS n,
 AVG(ABS(pred_p50-actual_p90_load)) AS mae_load_factor,
 AVG(CAST(actual_in_p10_p90 AS DOUBLE)) AS interval_coverage
FROM latest WHERE horizon_min=30 AND actual_data_origin='observed'
GROUP BY GROUPING SETS ((model_name,model_version),(route_short_name,model_name,model_version))
ORDER BY CASE WHEN route_short_name='ALL ROUTES' THEN 0 ELSE 1 END,route_short_name""",
    ),
    (
        EVENT_QUESTION,
        """SELECT title,location_title,venue_key,is_all_day,
 CASE WHEN is_all_day THEN concat(date_format(from_utc_timestamp(start_ts,'America/Chicago'),'yyyy-MM-dd'),' (all day)')
 ELSE concat(date_format(from_utc_timestamp(start_ts,'America/Chicago'),'yyyy-MM-dd HH:mm'),' America/Chicago') END AS starts_local,
 significance,est_attendance,
 2*6371000*asin(sqrt(pow(sin(radians(lat-30.6102)/2),2)+cos(radians(30.6102))*cos(radians(lat))*pow(sin(radians(lon+96.3404)/2),2))) AS distance_m
FROM transit.silver.events
WHERE NOT is_canceled AND lat IS NOT NULL AND lon IS NOT NULL
 AND to_date(from_utc_timestamp(start_ts,'America/Chicago')) >=
 date_add(to_date(date_trunc('week',from_utc_timestamp(current_timestamp(),'America/Chicago'))),7)
 AND to_date(from_utc_timestamp(start_ts,'America/Chicago')) <
 date_add(to_date(date_trunc('week',from_utc_timestamp(current_timestamp(),'America/Chicago'))),14)
 AND 2*6371000*asin(sqrt(pow(sin(radians(lat-30.6102)/2),2)+cos(radians(30.6102))*cos(radians(lat))*pow(sin(radians(lon+96.3404)/2),2))) <=600
ORDER BY significance DESC NULLS LAST,start_ts""",
    ),
    (
        "How full was route 01-04 at 2pm yesterday?",
        """SELECT route_short_name,direction_name,
 from_utc_timestamp(window_start,'America/Chicago') AS window_local,
 avg_load_factor,p90_load_factor,max_load_factor,n_pings
FROM transit.gold.route_dir_15m
WHERE route_short_name='01-04' AND data_origin='observed'
 AND to_date(from_utc_timestamp(window_start,'America/Chicago')) =
 date_sub(to_date(from_utc_timestamp(current_timestamp(),'America/Chicago')),1)
 AND hour(from_utc_timestamp(window_start,'America/Chicago'))=14
 AND minute(from_utc_timestamp(window_start,'America/Chicago'))=0
ORDER BY direction_id""",
    ),
]


def uid(value: str) -> str:
    return hashlib.md5(value.encode(), usedforsecurity=False).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def build(workspace):
    sql = SqlClient(workspace)
    _tables, columns = collect_metadata(sql)
    inventory = sql.rows(
        "SELECT table_schema,table_name FROM transit.information_schema.tables WHERE table_schema IN ('bronze','silver','gold') AND substring(table_name,1,2) <> '__' ORDER BY table_schema,table_name"
    )
    snapshot = {
        f"transit.{t['table_schema']}.{t['table_name']}": [
            {"name": c["column_name"], "type": c["data_type"]}
            for c in columns
            if (c["table_schema"], c["table_name"])
            == (t["table_schema"], t["table_name"])
        ]
        for t in inventory
    }
    write_json(DASHBOARDS / "catalog_snapshot.json", snapshot)
    datasets, pages = [], []

    def dataset(name, query):
        datasets.append(
            {
                "name": name,
                "displayName": name.replace("_", " ").title(),
                "queryLines": query.splitlines(keepends=True),
            }
        )
        return name

    def page(name, title):
        p = {
            "name": name,
            "displayName": title,
            "pageType": "PAGE_TYPE_CANVAS",
            "layout": [],
        }
        pages.append(p)
        return p

    def widget(
        p,
        name,
        title,
        description,
        fields,
        kind="table",
        x=None,
        y=None,
        color=None,
        xt="categorical",
        position=None,
    ):
        frame = {
            "title": title,
            "description": description,
            "showTitle": True,
            "showDescription": True,
        }
        if kind == "counter":
            spec = {
                "version": 2,
                "widgetType": "counter",
                "encodings": {
                    "value": {"fieldName": fields[0][0], "displayName": title}
                },
                "frame": frame,
            }
        elif kind == "table":
            spec = {
                "version": 1,
                "widgetType": "table",
                "frame": frame,
                "encodings": {
                    "columns": [
                        {
                            "fieldName": n,
                            "title": n.replace("_", " ").title(),
                            "type": t,
                            "displayAs": "datetime"
                            if t == "date"
                            else "string"
                            if t == "string"
                            else "number"
                            if t in ("integer", "float")
                            else t,
                            "booleanValues": ["false", "true"],
                            "visible": True,
                            "order": i,
                        }
                        for i, (n, t) in enumerate(fields)
                    ]
                },
                "invisibleColumns": [],
                "itemsPerPage": 10,
                "condensed": True,
            }
        else:
            enc = {
                "x": {"fieldName": x, "scale": {"type": xt}},
                "y": {"fieldName": y, "scale": {"type": "quantitative"}},
            }
            if color:
                enc["color"] = {"fieldName": color, "scale": {"type": "categorical"}}
            spec = {"version": 3, "widgetType": kind, "encodings": enc, "frame": frame}
        p["layout"].append(
            {
                "widget": {
                    "name": name + "_" + str(len(p["layout"])),
                    "queries": [
                        {
                            "name": "main_query",
                            "query": {
                                "datasetName": name,
                                "fields": [
                                    {"name": n, "expression": f"`{n}`"}
                                    for n, t in fields
                                ],
                                "disaggregated": True,
                            },
                        }
                    ],
                    "spec": spec,
                },
                "position": position
                or {"x": 0, "y": len(p["layout"]) * 7, "width": 6, "height": 7},
            }
        )

    live = page("live_ops", "Live operations")
    dataset(
        "fleet_now",
        """SELECT COUNT_IF(ts_gps >= current_timestamp()-INTERVAL 5 MINUTES) AS active_buses,
 coalesce(SUM(CASE WHEN ts_gps >= current_timestamp()-INTERVAL 5 MINUTES THEN onboard END),0) AS total_onboard,
 COUNT_IF(ts_gps >= current_timestamp()-INTERVAL 5 MINUTES AND load_factor>=0.75) AS crowded_buses,
 timestampdiff(SECOND,MAX(ts_gps),current_timestamp()) AS freshest_gps_lag_s,
 MAX(ts_gps) AS max_ts_gps_utc
FROM transit.silver.vehicle_latest WHERE data_origin='observed'""",
    )
    for i, (field, title, label) in enumerate(
        [
            (
                "active_buses",
                "Active buses · DERIVED · GPS",
                "Distinct latest vehicles with GPS fixes within five minutes.",
            ),
            (
                "total_onboard",
                "Total onboard · MEASURED · APC",
                "Reported people on fresh active buses; APC accuracy unverified.",
            ),
            (
                "crowded_buses",
                "Buses ≥75% full · MEASURED · APC",
                "Fresh active buses with onboard/capacity >=0.75.",
            ),
            (
                "freshest_gps_lag_s",
                "Freshest GPS lag (seconds) · DERIVED · GPS",
                "Current UTC minus max(ts_gps), including stale rows. NULL means no readings.",
            ),
        ]
    ):
        widget(
            live,
            "fleet_now",
            title,
            label,
            [(field, "integer")],
            kind="counter",
            position={"x": i % 2 * 3, "y": i // 2 * 3, "width": 3, "height": 3},
        )
    dataset(
        "route_now",
        """SELECT route_short_name,COUNT(*) AS active_buses,SUM(onboard) AS onboard,
 SUM(capacity) AS capacity,try_divide(SUM(onboard),SUM(capacity)) AS fleet_load_factor,
 MAX(load_factor) AS max_bus_load_factor,MAX(ts_gps) AS max_ts_gps_utc
FROM transit.silver.vehicle_latest
WHERE data_origin='observed' AND ts_gps>=current_timestamp()-INTERVAL 5 MINUTES
GROUP BY route_short_name ORDER BY fleet_load_factor DESC""",
    )
    widget(
        live,
        "route_now",
        "Load by route now · MEASURED · APC",
        "Weighted onboard/capacity across fresh buses, fraction; max bus load preserves crowding extremes.",
        [
            ("route_short_name", "string"),
            ("active_buses", "integer"),
            ("onboard", "integer"),
            ("capacity", "integer"),
            ("fleet_load_factor", "float"),
            ("max_bus_load_factor", "float"),
            ("max_ts_gps_utc", "datetime"),
        ],
        position={"x": 0, "y": 6, "width": 6, "height": 8},
    )

    service = page("service_quality", "Service quality")
    dataset(
        "headway_hour",
        """SELECT route_short_name,direction_id,date_trunc('hour',arrive_ts) AS hour_utc,
 COUNT(*) AS n_headways,COUNT(headway_ratio) AS n_valid_ratios,
 try_divide(stddev_samp(obs_headway_s),AVG(obs_headway_s)) AS headway_cv,
 AVG(CAST(is_bunched AS DOUBLE)) AS bunching_rate,
 AVG(CAST(is_gap AS DOUBLE)) AS gap_rate,SUM(CAST(is_gap AS INT)) AS gaps
FROM transit.silver.headways WHERE data_origin='observed' AND arrive_ts>=current_timestamp()-INTERVAL 7 DAYS
GROUP BY route_short_name,direction_id,date_trunc('hour',arrive_ts) ORDER BY hour_utc DESC,route_short_name""",
    )
    widget(
        service,
        "headway_hour",
        "Headway CV, bunching and gaps by route/hour · DERIVED · GPS",
        "UTC hours; CV=sample stddev/mean seconds; bunching=ratio<0.5, gap=ratio>1.5; NULL ratios excluded from rates. Observed only, last 7 days.",
        [
            ("route_short_name", "string"),
            ("direction_id", "integer"),
            ("hour_utc", "datetime"),
            ("n_headways", "integer"),
            ("n_valid_ratios", "integer"),
            ("headway_cv", "float"),
            ("bunching_rate", "float"),
            ("gap_rate", "float"),
            ("gaps", "integer"),
        ],
    )
    dataset(
        "stop_visits",
        """SELECT v.route_short_name,v.stop_id,s.stop_name,COUNT(*) AS n_visits,
 AVG(v.dwell_s) AS avg_dwell_s,SUM(v.net_board_proxy) AS net_board_proxy
FROM transit.silver.stop_visits v LEFT JOIN
 (SELECT stop_id,MAX(stop_name) AS stop_name FROM transit.silver.gtfs_stops GROUP BY stop_id) s USING(stop_id)
WHERE v.data_origin='observed' AND v.arrive_ts>=current_timestamp()-INTERVAL 7 DAYS
GROUP BY v.route_short_name,v.stop_id,s.stop_name ORDER BY n_visits DESC""",
    )
    widget(
        service,
        "stop_visits",
        "Stop visits and dwell · DERIVED · GPS",
        "Observed inferred visits, last 7 days. Net boarding proxy is net onboard change, never gross boardings.",
        [
            ("route_short_name", "string"),
            ("stop_id", "string"),
            ("stop_name", "string"),
            ("n_visits", "integer"),
            ("avg_dwell_s", "float"),
            ("net_board_proxy", "integer"),
        ],
    )

    quality = page("forecast_quality", "Forecast quality")
    dataset(
        "forecast_metrics",
        DEDUP
        + """SELECT horizon_min,route_short_name,actual_data_origin,model_name,model_version,
 COUNT(*) AS n,AVG(ABS(pred_p50-actual_p90_load)) AS mae_load_factor,
 AVG(CAST(actual_in_p10_p90 AS DOUBLE)) AS interval_coverage
FROM latest GROUP BY horizon_min,route_short_name,actual_data_origin,model_name,model_version
ORDER BY actual_data_origin,horizon_min,route_short_name""",
    )
    metric_fields = [
        ("horizon_min", "integer"),
        ("route_short_name", "string"),
        ("actual_data_origin", "string"),
        ("model_name", "string"),
        ("model_version", "string"),
        ("n", "integer"),
        ("mae_load_factor", "float"),
        ("interval_coverage", "float"),
    ]
    widget(
        quality,
        "forecast_metrics",
        "MAE and interval coverage · PREDICTED vs MEASURED · APC / SIMULATED",
        "Separate actual_data_origin rows. Latest score per target example; n=matched forecasts. MAE is fraction (0.01=1 percentage point). p10–p90 nominal coverage 80%. Rolling scored matches, not necessarily independent holdout.",
        metric_fields,
    )
    for origin in ["observed", "simulated"]:
        name = "forecast_" + origin
        dataset(
            name,
            DEDUP
            + f"""SELECT date_trunc('hour',target_window_start) AS target_hour_utc,
 concat(route_short_name,' / ',horizon_min,'m / ',series) AS series,
 AVG(load_factor) AS load_factor,COUNT(*) AS n
FROM (SELECT *, 'predicted median' AS series,pred_p50 AS load_factor FROM latest WHERE actual_data_origin='{origin}'
 UNION ALL SELECT *, 'actual p90' AS series,actual_p90_load AS load_factor FROM latest WHERE actual_data_origin='{origin}')
GROUP BY date_trunc('hour',target_window_start),route_short_name,horizon_min,series ORDER BY target_hour_utc""",
        )
        widget(
            quality,
            name,
            "Forecast vs "
            + origin
            + " · PREDICTED / "
            + ("MEASURED · APC" if origin == "observed" else "SIMULATED"),
            "Hourly averages by route and horizon, UTC. Only matched "
            + origin
            + " targets; lines show median forecast and actual window p90, fractions.",
            [
                ("target_hour_utc", "datetime"),
                ("series", "string"),
                ("load_factor", "float"),
                ("n", "integer"),
            ],
            kind="line",
            x="target_hour_utc",
            y="load_factor",
            color="series",
            xt="temporal",
        )
    champions = []
    for h in [15, 30, 60]:
        model = workspace.model_versions.get_by_alias(
            f"transit.ml.crowding_h{h}", "champion"
        )
        champions.append(
            {
                "model_name": f"transit.ml.crowding_h{h}",
                "champion_version": str(model.version),
            }
        )
    now = datetime.now(UTC).isoformat()
    write_json(
        DASHBOARDS / "champion_snapshot.json",
        {"resolved_utc": now, "models": champions},
    )
    values = ",".join(
        "('" + m["model_name"] + "','" + m["champion_version"] + "','" + now + "')"
        for m in champions
    )
    dataset(
        "champion_models",
        "SELECT * FROM VALUES "
        + values
        + " AS champions(model_name,champion_version,resolved_utc)",
    )
    widget(
        quality,
        "champion_models",
        "Champion model versions · PREDICTED",
        "UC @champion aliases resolved at asset build time shown below; regenerate to refresh after promotion. Evaluation rows show actual scored model_version.",
        [
            ("model_name", "string"),
            ("champion_version", "string"),
            ("resolved_utc", "string"),
        ],
    )

    dq = page("data_quality", "Data quality and lineage")
    logs = """WITH logs AS (
 SELECT timestamp,origin,details FROM transit.silver.event_log_206b4370_b5cc_48eb_8eba_932dcea5dc95 WHERE event_type='flow_progress'
 UNION ALL
 SELECT timestamp,origin,details FROM transit.silver.event_log_3db97dca_16f4_4d27_88eb_3563ac1749ee WHERE event_type='flow_progress'
), completed AS (
 SELECT *,row_number() OVER (PARTITION BY origin.pipeline_id,origin.flow_name ORDER BY timestamp DESC) AS rn
 FROM logs WHERE get_json_object(details,'$.flow_progress.status')='COMPLETED'
)
"""
    dataset(
        "expectation_rates",
        logs
        + """SELECT origin.pipeline_name AS pipeline,origin.flow_name AS flow,timestamp AS updated_utc,
 e.name AS expectation,e.passed_records,e.failed_records,
 try_divide(e.passed_records,e.passed_records+e.failed_records) AS pass_rate
FROM completed LATERAL VIEW explode(from_json(get_json_object(details,'$.flow_progress.data_quality.expectations'),
 'array<struct<name:string,dataset:string,passed_records:bigint,failed_records:bigint>>')) x AS e
WHERE rn=1 ORDER BY pipeline,flow,expectation""",
    )
    widget(
        dq,
        "expectation_rates",
        "Latest flow expectation pass rates · DERIVED · pipeline event log",
        "Latest completed flow per pipeline; pass/(pass+fail), not 1-dropped/output. Multiple expectations can test the same row; do not sum failures as unique dropped rows.",
        [
            ("pipeline", "string"),
            ("flow", "string"),
            ("updated_utc", "datetime"),
            ("expectation", "string"),
            ("passed_records", "integer"),
            ("failed_records", "integer"),
            ("pass_rate", "float"),
        ],
    )
    dataset(
        "pipeline_drops",
        logs
        + """SELECT origin.pipeline_name AS pipeline,origin.flow_name AS flow,timestamp AS updated_utc,
 CAST(get_json_object(details,'$.flow_progress.metrics.num_output_rows') AS BIGINT) AS output_rows,
 CAST(get_json_object(details,'$.flow_progress.data_quality.dropped_records') AS BIGINT) AS dropped_rows,
 CAST(get_json_object(details,'$.flow_progress.data_quality.warned_records') AS BIGINT) AS warned_rows
FROM completed WHERE rn=1 ORDER BY pipeline,flow""",
    )
    widget(
        dq,
        "pipeline_drops",
        "Output, dropped and warned rows · DERIVED · pipeline event log",
        "Latest completed flow record, not cumulative run totals. Pipeline graph / UC lineage: core 206b4370-b5cc-48eb-8eba-932dcea5dc95 → ops 3db97dca-16f4-4d27-88eb-3563ac1749ee → gold → MLflow /Shared/aggiepulse → predictions.",
        [
            ("pipeline", "string"),
            ("flow", "string"),
            ("updated_utc", "datetime"),
            ("output_rows", "integer"),
            ("dropped_rows", "integer"),
            ("warned_rows", "integer"),
        ],
    )
    dataset(
        "event_filter",
        "SELECT reason,raw_count,kept_count,dropped_count FROM transit.silver.events_filter_stats ORDER BY raw_count DESC",
    )
    widget(
        dq,
        "event_filter",
        "Calendar raw vs kept · DERIVED · LiveWhale filter",
        "Reason-level counts; sum raw/kept/dropped for full evaluated set. Noise and non-campus/virtual events are rejected.",
        [
            ("reason", "string"),
            ("raw_count", "integer"),
            ("kept_count", "integer"),
            ("dropped_count", "integer"),
        ],
    )
    dataset(
        "layer_counts",
        "\nUNION ALL\n".join(
            f"SELECT '{t['table_schema']}' AS layer,'{t['table_name']}' AS table_name,COUNT(*) AS row_count FROM transit.{t['table_schema']}.{t['table_name']}"
            for t in inventory
        ),
    )
    widget(
        dq,
        "layer_counts",
        "Rows by table and layer · DERIVED · UC table census",
        "Live COUNT(*) over all public Bronze/Silver/Gold tables at asset build discovery. Counts across grains are not unique passenger totals; internal materializations excluded.",
        [("layer", "string"), ("table_name", "string"), ("row_count", "integer")],
    )
    split_tables = [
        ("silver", "vehicle_pings", "data_origin"),
        ("silver", "headways", "data_origin"),
        ("gold", "route_dir_15m", "data_origin"),
        ("gold", "route_dir_15m_sim", "data_origin"),
        ("gold", "training_set", "data_origin"),
        ("gold", "pred_vs_actual", "actual_data_origin"),
    ]
    dataset(
        "origin_split",
        "\nUNION ALL\n".join(
            f"SELECT '{s}.{t}' AS table_name,coalesce({c},'unmatched') AS data_origin,COUNT(*) AS row_count FROM transit.{s}.{t} GROUP BY {c}"
            for s, t, c in split_tables
        ),
    )
    widget(
        dq,
        "origin_split",
        "Observed vs simulated rows · MEASURED · APC / DERIVED · GPS / SIMULATED",
        "Separate origins per table. pred_vs_actual uses actual_data_origin (unmatched=NULL), not training origin. training_set appears only in this census, excluded from Genie.",
        [("table_name", "string"), ("data_origin", "string"), ("row_count", "integer")],
    )

    demand = page("events_demand", "Events and demand")
    dataset(
        "upcoming_events",
        """SELECT title,location_title,venue_key,start_ts AS start_utc,
 from_utc_timestamp(start_ts,'America/Chicago') AS start_chicago,significance,est_attendance,is_football_home
FROM transit.silver.events WHERE NOT is_canceled AND start_ts>=current_timestamp()
 AND start_ts<current_timestamp()+INTERVAL 14 DAYS AND significance>0 ORDER BY significance DESC,start_ts""",
    )
    widget(
        demand,
        "upcoming_events",
        "Upcoming significant events · DERIVED · LiveWhale / SIMULATED prior",
        "Next 14 days; calendar times UTC and Chicago shown. Significance and attendance are heuristics, not measured demand or event turnout.",
        [
            ("title", "string"),
            ("location_title", "string"),
            ("venue_key", "string"),
            ("start_utc", "datetime"),
            ("start_chicago", "datetime"),
            ("significance", "float"),
            ("est_attendance", "integer"),
            ("is_football_home", "boolean"),
        ],
    )
    dataset(
        "exposure_by_route",
        """SELECT route_short_name,direction_id,MIN(window_start) AS first_window_utc,MAX(window_start) AS last_window_utc,
 MAX(event_exposure) AS peak_available_exposure,
 MAX(CASE WHEN window_start>=current_timestamp() THEN event_exposure END) AS peak_future_exposure,MAX(football_home_game_day) AS football_home_game_day,
 MAX(event_timing_is_assumed) AS any_assumed_timing,COUNT(*) AS n_windows
FROM transit.gold.event_exposure
GROUP BY route_short_name,direction_id ORDER BY peak_available_exposure DESC""",
    )
    widget(
        demand,
        "exposure_by_route",
        "Event exposure by route · DERIVED · GPS / SIMULATED prior",
        "Available table coverage shown explicitly; peak_future_exposure is NULL without future windows. Calendar proximity priors near stops (600 m), not causal effects.",
        [
            ("route_short_name", "string"),
            ("direction_id", "integer"),
            ("first_window_utc", "datetime"),
            ("last_window_utc", "datetime"),
            ("peak_available_exposure", "float"),
            ("peak_future_exposure", "float"),
            ("football_home_game_day", "float"),
            ("any_assumed_timing", "float"),
            ("n_windows", "integer"),
        ],
    )
    dataset(
        "ntd_trend",
        """SELECT month,SUM(upt) AS boardings FROM transit.silver.ntd_monthly
WHERE ntd_id='60278' AND mode='MB' AND tos='DO' GROUP BY month ORDER BY month""",
    )
    widget(
        demand,
        "ntd_trend",
        "TAMU monthly boardings · MEASURED · FTA NTD",
        "Agency 60278, motor bus, directly operated. UPT counts unlinked boardings, not unique students or APC occupancy; historical calibration context.",
        [("month", "date"), ("boardings", "integer")],
        kind="line",
        x="month",
        y="boardings",
        xt="temporal",
    )
    write_json(
        DASHBOARDS / "aggiepulse_ops.lvdash.json",
        {"datasets": datasets, "pages": pages},
    )

    curated = [
        f"transit.{s}.{t}"
        for s, t in [
            ("silver", "vehicle_latest"),
            ("silver", "vehicle_pings"),
            ("silver", "gtfs_routes"),
            ("silver", "gtfs_stops"),
            ("silver", "gtfs_trips"),
            ("silver", "schedule_expanded"),
            ("silver", "tripspark_routes"),
            ("silver", "service_alerts"),
            ("silver", "events"),
            ("silver", "events_filter_stats"),
            ("silver", "weather_hourly"),
            ("silver", "academic_days"),
            ("silver", "class_periods"),
            ("silver", "ntd_monthly"),
            ("silver", "route_direction_map"),
            ("silver", "stop_visits"),
            ("silver", "headways"),
            ("gold", "route_dir_15m"),
            ("gold", "stop_15m"),
            ("gold", "route_dir_15m_sim"),
            ("gold", "event_exposure"),
            ("gold", "seasonal_profile"),
            ("gold", "predictions"),
            ("gold", "predictions_latest"),
            ("gold", "pred_vs_actual"),
        ]
    ]
    general = (
        """You are AggiePulse Transit Analyst for Texas A&M bus crowding. Use only curated Silver/Gold tables, never Bronze, internal materializations or training_set.
All stored timestamps are UTC. Local user time is America/Chicago; use from_utc_timestamp(ts,'America/Chicago') for clock/calendar grouping and to_utc_timestamp(local,'America/Chicago') for input boundaries. CURRENT_DATE is not necessarily Chicago today: derive to_date(from_utc_timestamp(current_timestamp(),'America/Chicago')). Weeks start Monday; 'this weekend' means Saturday/Sunday of the current local Monday-start week, not the last seven days. 'Next week' is next Monday through Sunday. Never manufacture history: collection began November 7, 2026; report no observed data for earlier periods.
MEASURED · APC = bus-reported passengers; sensor accuracy unverified. DERIVED · GPS = inferred stop arrivals/headways, approximately ±15 seconds at 30-second polling, plus schedule/context-derived data. PREDICTED = model output. SIMULATED = synthetic history and assumed event effects. Never mix observed/simulated unless explicitly asked; always label and group them separately. data_origin is current row provenance. actual_data_origin is evaluation truth provenance; training_data_origin describes training, NOT evaluation truth. Missing actuals/NULLs are unknown, never zero.
load_factor=onboard/capacity, a fraction; 0.75 means 75% full, 'crowded' is >=0.75. It can exceed 1.0. Do not confuse mean, p90 and max. Prediction target is future-window p90 load in [t0+h,t0+h+15min), horizons 15/30/60 minutes. pred_p10/p50/p90 are predictive quantiles of that target; p_crowd=P(future maximum load>=0.75). For model-wide MAE return an ALL ROUTES aggregate first, with n and coverage, then optional route breakdown. Never average per-route MAEs without weighting by n. MAE=AVG(ABS(pred_p50-actual_p90_load)), fraction; multiply by 100 only when labeling percentage points. Coverage=AVG(CAST(actual_in_p10_p90 AS DOUBLE)), nominal 80%. Exclude unmatched actuals; show n and model_version. Keep latest scoring_ts per t0/route_short_name/direction_id/horizon_min/actual_data_origin before aggregation. Scored matches are not automatically independent held-out examples. Do not claim model beats baselines without explicit observed holdout evidence.
Headway ratio=obs_headway_s/sched_headway_s; bunching <0.5, gap >1.5. Rates exclude NULL ratio. Headway CV=stddev_samp(obs_headway_s)/avg(obs_headway_s). When asked which routes bunched, use silver.headways with observed origin, valid ratios, n and local date boundaries. A zero observed bunching rate with valid n is a real result, not missing data. net_board_proxy=onboard_after-onboard_before, NOT gross boardings. GPS speed units unverified; never assume mph/kph.
Route names are strings with leading zeros: 01 is Bonfire; 01-04 and 03-05 and 40-41 are distinct combined Break, Nights, & Weekends services, not numeric ranges. Keep hyphens intact. direction_key is TripSpark GUID; direction_id is GTFS 0/1, map with route_direction_map.is_selected=true. Join GTFS versions consistently, avoid multiplying route/stop rows.
Events: significance and attendance are heuristic priors, not measured turnout or causal demand. event_exposure uses proximity to route stops within 600 m with temporal weights; geospatial method may not be H3. For 'near Kyle Field', use <=600 m haversine from 30.6102,-96.3404, not only venue_name=Kyle Field: include adjacent venues. Exclude canceled events. Next week means local next Monday inclusive through following Monday exclusive. All-day events (is_all_day=true) at midnight denote a calendar date, never a game kickoff time; explicitly label all-day versus timed events. List highest-significance events first (NULL significance last), followed by start time, and use the human-readable starts_local text from the trusted query. Calendar coverage can be incomplete; a missing event is not proof it will not occur. Event uplift is model attribution with simulated prior, never causal impact.
NTD UPT = monthly unlinked boardings, not unique riders; TAMU ntd_id=60278, mode MB, tos DO. Event raw/kept/dropped stats are reason-group totals. Forecast champion versions below are a build-time snapshot; prediction model_version is the version actually scored. Active buses require vehicle_latest observed and ts_gps within five minutes. If stale, disclose the freshest GPS lag and show no fresh buses.
"""
        + "Champion alias snapshot: "
        + json.dumps({"resolved_utc": now, "models": champions})
    )
    # Include column-level definitions even when UC ALTER is blocked by pipeline ownership.
    text_instructions = [{"id": uid("general"), "content": [general]}]
    for table in sorted(k for k in snapshot if not k.startswith("transit.bronze.")):
        _, schema, name = table.split(".")
        description = table_comment(schema, name).split(
            " All TIMESTAMP columns are UTC;"
        )[0]
        if table not in curated:
            description = (
                "REFERENCE GLOSSARY ONLY, NOT AN ALLOWED QUERY SOURCE. " + description
            )
        docs = ", ".join(
            c["column_name"]
            for c in columns
            if c["table_schema"] == schema and c["table_name"] == name
        )
        text_instructions.append(
            {
                "id": uid(table),
                "content": [
                    table
                    + " — "
                    + description
                    + "\nColumns (definitions in shared glossary): "
                    + docs
                ],
            }
        )
    glossary = {}
    for column in columns:
        glossary[column["column_name"]] = column_comment(
            column["column_name"], column["data_type"]
        )
    text_instructions.append(
        {
            "id": uid("column_glossary"),
            "content": [
                "SHARED COLUMN GLOSSARY. Definitions apply to every table listing the column above, including pipeline views whose UC column ALTER is unsupported. Table-level provenance takes precedence.\n"
                + "\n".join(
                    name + ": " + description
                    for name, description in sorted(glossary.items())
                )
            ],
        }
    )
    instruction_content = [
        "".join(item["content"]) + "\n\n" for item in text_instructions
    ]
    if sum(map(len, instruction_content)) > 65000:
        raise ValueError("Genie instructions exceed API 65000-character total limit")
    questions = [q for q, _ in EXAMPLES] + [
        "Which buses are at least 75% full right now?",
        "How fresh is the latest GPS reading?",
        "Which stops had the largest service gaps today?",
        "How many calendar events were kept versus filtered out?",
        "How does simulated route crowding differ from observed crowding?",
        "What is TAMU monthly NTD ridership over the last year?",
        "Which model versions produced the latest forecasts?",
        "What service alerts affect route 01-04 next week?",
    ]
    space = {
        "version": 2,
        "config": {
            "sample_questions": [{"id": uid(q), "question": [q]} for q in questions]
        },
        "data_sources": {
            "tables": [
                {"identifier": t, "description": [table_comment(*t.split(".")[1:])]}
                for t in sorted(curated)
            ]
        },
        "instructions": {
            "text_instructions": [
                {
                    "id": uid("general"),
                    "content": instruction_content,
                }
            ],
            "example_question_sqls": sorted(
                [
                    {"id": uid(q), "question": [q], "sql": [query]}
                    for q, query in EXAMPLES
                ],
                key=lambda x: x["id"],
            ),
        },
    }
    write_json(DASHBOARDS / "aggiepulse_analyst.geniespace.json", space)
    print(
        f"Built {len(datasets)} datasets, {len(pages)} pages, {len(curated)} Genie tables, {len(questions)} questions"
    )


if __name__ == "__main__":
    from databricks.sdk import WorkspaceClient

    build(WorkspaceClient(profile="transit"))
