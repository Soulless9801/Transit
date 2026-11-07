"""Triggered Lakeflow operations layer; references core tables as external inputs.

Static GTFS and service-day schedules stay in Spark and are attached once to
packed vehicle-day groups. Pandas receives no Spark objects and no dataset
function performs a driver action. All scheduled windows are generated in Spark.
"""

import sys

import pandas as pd
from pyspark import pipelines as dp
from pyspark.sql import Window
from pyspark.sql import functions as F

# Workspace source paths are distributed with Python UDFs on serverless compute.
sys.path.insert(0, spark.conf.get("aggiepulse.source_path"))  # noqa: F821
from aggiepulse.transform import (
    build_patterns,
    build_shapes,
    detect_stop_visits,
    route_direction_map,
)
from aggiepulse.transform.operations import MAP_COLUMNS, VISIT_COLUMNS

CAT = spark.conf.get("aggiepulse.catalog", "transit")  # noqa: F821
spark.conf.set("spark.sql.session.timeZone", "UTC")  # noqa: F821

MAP_SCHEMA = """route_short_name string, direction_key string, route_id string,
 direction_id int, shape_id string, median_offset_m double, fit_quality double,
 backward_fraction double, candidate_rank int, is_selected boolean, n_pings int,
 data_origin string"""
VISIT_SCHEMA = """vehicle_id string, route_short_name string, direction_id int,
 direction_name string, stop_id string, stop_sequence int, arrive_ts timestamp,
 depart_ts timestamp, dwell_s double, onboard_before int, onboard_after int,
 net_board_proxy int, shape_dist_m double, matched_trip_id string, sched_ts timestamp,
 delay_s double, data_origin string"""
RK = ["route_short_name", "direction_id", "window_start", "data_origin"]
SK = ["stop_id", "window_start", "data_origin"]
HK = ["stop_id", "route_short_name", "direction_id"]


def name(layer, table):
    return f"{CAT}.{layer}.{table}"


def read(layer, table):
    return spark.read.table(name(layer, table))  # noqa: F821


def reference_rows(include_patterns=False):
    """One compact reference row, computed in Spark without driver actions.

    Keep the longest stop pattern per shape (the pandas pattern policy), so a
    vehicle-day receives hundreds of static stops rather than 58k stop_times.
    """
    routes = read("silver", "gtfs_routes").select("route_id", "route_short_name")
    trips = read("silver", "gtfs_trips").select(
        "trip_id", "route_id", "direction_id", "shape_id"
    )
    shapes = read("silver", "gtfs_shapes").select(
        "shape_id",
        "shape_pt_sequence",
        "shape_pt_lat",
        "shape_pt_lon",
        "shape_dist_traveled",
    )
    frames = [(routes, "_routes"), (trips, "_trips"), (shapes, "_shapes")]
    if include_patterns:
        times = read("silver", "gtfs_stop_times").select(
            "trip_id", "stop_id", "stop_sequence", "shape_dist_traveled"
        )
        counts = times.groupBy("trip_id").count().join(trips, "trip_id")
        chosen = counts.withColumn(
            "_rank",
            F.row_number().over(
                Window.partitionBy("shape_id").orderBy(F.desc("count"), "trip_id")
            ),
        ).where("_rank = 1")
        frames[1] = (trips.join(chosen.select("trip_id"), "trip_id"), "_trips")
        frames += [
            (times.join(chosen.select("trip_id"), "trip_id"), "_stop_times"),
            (
                read("silver", "gtfs_stops").select("stop_id", "stop_lat", "stop_lon"),
                "_stops",
            ),
        ]
    result = None
    for frame, column in frames:
        packed = frame.agg(F.collect_list(F.struct(*frame.columns)).alias(column))
        result = packed if result is None else result.crossJoin(packed)
    return result


def unpack_references(row, include_patterns=False):
    routes = pd.DataFrame(list(row["_routes"]))
    trips = pd.DataFrame(list(row["_trips"]))
    shapes = build_shapes(pd.DataFrame(list(row["_shapes"])))
    patterns = None
    if include_patterns:
        patterns = build_patterns(
            trips,
            pd.DataFrame(list(row["_stop_times"])),
            pd.DataFrame(list(row["_stops"])),
            shapes,
        )
    return routes, trips, shapes, patterns


def pings():
    return read("silver", "vehicle_pings").where(
        "data_origin IN ('observed', 'simulated')"
    )


def mapped_pings():
    mapping = (
        read("silver", "route_direction_map")
        .where("is_selected")
        .select("route_short_name", "direction_key", "data_origin", "direction_id")
    )
    return pings().join(
        mapping, ["route_short_name", "direction_key", "data_origin"], "inner"
    )


@dp.materialized_view(name=name("silver", "route_direction_map"))
def direction_mapping():
    ping = pings()
    keys = ["route_short_name", "direction_key", "data_origin"]
    packed = ping.groupBy(*keys).agg(
        F.collect_list(F.struct(*ping.columns)).alias("_pings")
    )
    inputs = packed.crossJoin(F.broadcast(reference_rows()))

    def fit(pdf):
        row = pdf.iloc[0]
        routes, trips, shapes, _ = unpack_references(row)
        return route_direction_map(
            pd.DataFrame(list(row["_pings"])), routes, trips, shapes
        )

    return inputs.groupBy(*keys).applyInPandas(fit, schema=MAP_SCHEMA)


@dp.materialized_view(name=name("silver", "stop_visits"))
@dp.expect_or_drop("valid_visit", "depart_ts >= arrive_ts AND dwell_s >= 0")
def stop_visits():
    ping = pings().withColumn(
        "service_date", F.to_date(F.from_utc_timestamp("ts_gps", "America/Chicago"))
    )
    route_map = read("silver", "route_direction_map")
    # Join candidates as structs, retaining the input ping schema unambiguously.
    joined = (
        ping.alias("p")
        .join(
            route_map.alias("m"),
            ["route_short_name", "direction_key", "data_origin"],
            "inner",
        )
        .select(
            "p.*",
            F.struct(*[F.col("m." + key).alias(key) for key in MAP_COLUMNS]).alias(
                "_mapping"
            ),
        )
    )
    keys = ["vehicle_id", "service_date", "data_origin"]
    packed = joined.groupBy(*keys).agg(
        F.collect_set(F.struct(*[F.col(key) for key in ping.columns])).alias("_pings"),
        F.collect_set("_mapping").alias("_mapping"),
    )
    schedule = read("silver", "schedule_expanded").select(
        "service_date",
        "trip_id",
        "route_short_name",
        "direction_id",
        "stop_id",
        "stop_sequence",
        "sched_arrival_ts",
        "is_timepoint",
    )
    # Day D can include the previous service day's >24:00 trips.
    schedule = schedule.withColumn("_vehicle_day", F.col("service_date")).unionByName(
        schedule.withColumn("_vehicle_day", F.date_add("service_date", 1))
    )
    schedule = schedule.groupBy("_vehicle_day").agg(
        F.collect_list(
            F.struct(*[F.col(key) for key in schedule.columns if key != "_vehicle_day"])
        ).alias("_schedule")
    )
    inputs = packed.join(
        schedule, packed.service_date == schedule._vehicle_day, "left"
    ).crossJoin(F.broadcast(reference_rows(include_patterns=True)))

    def visits(pdf):
        row = pdf.iloc[0]
        _, _, shapes, patterns = unpack_references(row, include_patterns=True)
        schedule_rows = row["_schedule"]
        expanded = (
            pd.DataFrame(list(schedule_rows)) if schedule_rows is not None else None
        )
        result = detect_stop_visits(
            pd.DataFrame(list(row["_pings"])),
            pd.DataFrame(list(row["_mapping"])),
            shapes,
            patterns,
            expanded,
        )
        for col in ["arrive_ts", "depart_ts", "sched_ts"]:
            result[col] = pd.to_datetime(result[col], utc=True).dt.tz_localize(None)
        return result[VISIT_COLUMNS]

    return inputs.groupBy(*keys).applyInPandas(visits, schema=VISIT_SCHEMA)


@dp.materialized_view(name=name("silver", "headways"))
def headways():
    observed_window = Window.partitionBy(*HK, "data_origin").orderBy(
        "arrive_ts", "vehicle_id"
    )
    visits = (
        read("silver", "stop_visits")
        .withColumn("prev_arrive_ts", F.lag("arrive_ts").over(observed_window))
        .withColumn(
            "obs_headway_s",
            F.col("arrive_ts").cast("double") - F.col("prev_arrive_ts").cast("double"),
        )
    )
    sched = (
        read("silver", "schedule_expanded").select(*HK, "sched_arrival_ts").distinct()
    )
    sched = sched.withColumn(
        "sched_headway_s",
        F.col("sched_arrival_ts").cast("double")
        - F.lag(F.col("sched_arrival_ts").cast("double")).over(
            Window.partitionBy(*HK).orderBy("sched_arrival_ts")
        ),
    )
    v, s = visits.alias("v"), sched.alias("s")
    condition = [F.col(f"v.{key}") == F.col(f"s.{key}") for key in HK]
    condition += [
        F.abs(
            F.col("v.arrive_ts").cast("double")
            - F.col("s.sched_arrival_ts").cast("double")
        )
        <= 3600
    ]
    joined = v.join(s, condition, "left").select(
        "v.*", "s.sched_arrival_ts", "s.sched_headway_s"
    )
    nearest = Window.partitionBy("vehicle_id", *HK, "arrive_ts", "data_origin").orderBy(
        F.abs(
            F.col("arrive_ts").cast("double") - F.col("sched_arrival_ts").cast("double")
        ),
        "sched_arrival_ts",
    )
    joined = joined.withColumn("_rank", F.row_number().over(nearest)).where("_rank = 1")
    joined = joined.withColumn(
        "headway_ratio",
        F.when(
            F.col("sched_headway_s") > 0,
            F.col("obs_headway_s") / F.col("sched_headway_s"),
        ),
    )
    return joined.select(
        *HK,
        "arrive_ts",
        "prev_arrive_ts",
        "obs_headway_s",
        "sched_headway_s",
        "headway_ratio",
        (F.col("headway_ratio") < 0.5).alias("is_bunched"),
        (F.col("headway_ratio") > 1.5).alias("is_gap"),
        "data_origin",
    )


def with_window(frame, timestamp):
    return frame.withColumn("window_start", F.window(timestamp, "15 minutes").start)


def schedule_grid():
    schedule = read("silver", "schedule_expanded")
    spans = schedule.groupBy(
        "route_short_name", "direction_id", "service_date", "trip_id"
    ).agg(
        F.min("sched_arrival_ts").alias("start"), F.max("sched_arrival_ts").alias("end")
    )
    spans = with_window(spans, "start").withColumnRenamed(
        "window_start", "first_window"
    )
    spans = with_window(spans, "end").withColumnRenamed("window_start", "last_window")
    return (
        spans.withColumn(
            "window_start",
            F.explode(
                F.sequence("first_window", "last_window", F.expr("INTERVAL 15 MINUTES"))
            ),
        )
        .groupBy(*RK[:-1])
        .agg(F.count("*").cast("int").alias("sched_trips"))
    )


@dp.materialized_view(name=name("gold", "route_dir_15m"))
def route_dir_15m():
    ping = mapped_pings()
    measured = (
        with_window(ping, "ts_gps")
        .groupBy(*RK)
        .agg(
            F.avg("load_factor").alias("avg_load_factor"),
            F.percentile("load_factor", 0.9).alias("p90_load_factor"),
            F.max("load_factor").alias("max_load_factor"),
            F.countDistinct("vehicle_id").cast("int").alias("active_vehicles"),
            F.count("*").cast("int").alias("n_pings"),
        )
    )
    origins = (
        ping.select("data_origin")
        .distinct()
        .union(spark.createDataFrame([("observed",)], "data_origin string"))  # noqa: F821
        .distinct()
    )
    grid = schedule_grid().crossJoin(origins)
    result = grid.join(measured, RK, "full").fillna({"sched_trips": 0, "n_pings": 0})
    hw = (
        with_window(read("silver", "headways"), "arrive_ts")
        .groupBy(*RK)
        .agg(
            (
                F.stddev_samp("obs_headway_s")
                / F.when(F.avg("obs_headway_s") > 0, F.avg("obs_headway_s"))
            ).alias("headway_cv"),
            F.avg(F.col("is_bunched").cast("double")).alias("bunching_rate"),
        )
    )
    delays = (
        with_window(read("silver", "stop_visits"), "arrive_ts")
        .groupBy(*RK)
        .agg(F.avg("delay_s").alias("mean_delay_s"))
    )
    names = ping.groupBy("route_short_name", "direction_id", "data_origin").agg(
        F.first("direction_name", ignorenulls=True).alias("direction_name")
    )
    # Schedule-only routes still get a readable static direction name.
    trip_names = (
        read("silver", "gtfs_trips")
        .join(read("silver", "gtfs_routes"), "route_id")
        .groupBy("route_short_name", "direction_id")
        .agg(F.first("trip_headsign", ignorenulls=True).alias("_name"))
    )
    result = (
        result.join(hw, RK, "left")
        .join(delays, RK, "left")
        .join(names, ["route_short_name", "direction_id", "data_origin"], "left")
        .join(trip_names, ["route_short_name", "direction_id"], "left")
        .withColumn("direction_name", F.coalesce("direction_name", "_name"))
    )
    return result.withColumn(
        "service_ratio",
        F.col("active_vehicles")
        / F.when(F.col("sched_trips") > 0, F.col("sched_trips")),
    ).select(
        "route_short_name",
        F.col("direction_id").cast("int"),
        "direction_name",
        "window_start",
        "avg_load_factor",
        "p90_load_factor",
        "max_load_factor",
        "active_vehicles",
        "sched_trips",
        "service_ratio",
        "headway_cv",
        "bunching_rate",
        "mean_delay_s",
        "n_pings",
        "data_origin",
    )


@dp.materialized_view(name=name("gold", "stop_15m"))
def stop_15m():
    visits = (
        with_window(read("silver", "stop_visits"), "arrive_ts")
        .groupBy(*SK)
        .agg(
            F.count("*").cast("int").alias("arrivals"),
            F.sum("net_board_proxy").alias("net_board_proxy_sum"),
        )
    )
    ratios = (
        with_window(read("silver", "headways"), "arrive_ts")
        .groupBy(*SK)
        .agg(F.avg("headway_ratio").alias("mean_headway_ratio"))
    )
    passing = (
        with_window(
            pings()
            .where("nearest_stop_dist_m <= 30")
            .withColumnRenamed("nearest_stop_id", "stop_id"),
            "ts_gps",
        )
        .groupBy(*SK)
        .agg(F.max("load_factor").alias("max_passing_load"))
    )
    return (
        visits.join(ratios, SK, "full")
        .join(passing, SK, "full")
        .select(
            *SK[:2],
            "arrivals",
            "mean_headway_ratio",
            "net_board_proxy_sum",
            "max_passing_load",
            "data_origin",
        )
    )
