"""Optional local Spark parity check (pyspark is not a production dependency).

Run with a compatible JDK and `uv run --with pyspark==4.0.1 python
 tests/transform/spark_check.py`. Lakeflow decorators are identity stubs; actual
Spark plans and pandas UDFs execute against small real GTFS/ping fixtures.
"""

import sys
import types
from pathlib import Path

import pandas as pd
from pyspark.sql import SparkSession

from aggiepulse.ingest.gtfs import latest_gtfs_zip, load_gtfs
from aggiepulse.transform import (
    build_patterns,
    build_shapes,
    detect_stop_visits,
    route_direction_map,
)
from aggiepulse.transform.aggregates import scheduled_grid
from aggiepulse.transform.local import expand_schedule, load_local_pings


def identity_decorator(*args, **kwargs):
    return lambda function: function


def run():
    import pyspark

    dp = types.ModuleType("pyspark.pipelines")
    dp.materialized_view = identity_decorator
    dp.expect_or_drop = identity_decorator
    sys.modules["pyspark.pipelines"] = dp
    pyspark.pipelines = dp
    spark = (
        SparkSession.builder.master("local[2]")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.warehouse.dir", "/tmp/aggiepulse-spark-warehouse")
        .config("aggiepulse.source_path", str(Path.cwd() / "src"))
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    namespace = {"spark": spark}
    source = Path("pipelines/ops/ops.py")
    exec(compile(source.read_text(), str(source), "exec"), namespace)  # noqa: S102
    tables = {}
    namespace["read"] = lambda layer, table: tables[(layer, table)]
    gtfs = load_gtfs(latest_gtfs_zip())
    pings = load_local_pings()
    pings["nearest_stop_id"] = "unassigned"
    pings["nearest_stop_dist_m"] = 999.0
    dates = set(pings.ts_gps.dt.tz_convert("America/Chicago").dt.date)
    sched = expand_schedule(gtfs, dates | {d - pd.Timedelta(days=1) for d in dates})
    for table in ["routes", "trips", "shapes", "stop_times", "stops"]:
        pdf = gtfs[table].copy()
        for key in ["direction_id", "stop_sequence", "shape_pt_sequence"]:
            if key in pdf:
                pdf[key] = pd.to_numeric(pdf[key]).astype(int)
        for key in [
            "shape_pt_lat",
            "shape_pt_lon",
            "shape_dist_traveled",
            "stop_lat",
            "stop_lon",
        ]:
            if key in pdf:
                pdf[key] = pd.to_numeric(pdf[key])
        # Nullable all-empty strings otherwise cannot have their type inferred.
        tables[("silver", "gtfs_" + table)] = spark.createDataFrame(pdf.fillna(""))
    tables[("silver", "vehicle_pings")] = spark.createDataFrame(pings)
    tables[("silver", "schedule_expanded")] = spark.createDataFrame(
        sched[
            [
                "service_date",
                "trip_id",
                "route_short_name",
                "direction_id",
                "stop_id",
                "stop_sequence",
                "sched_arrival_ts",
                "is_timepoint",
            ]
        ]
    )
    result = {}
    for table, function, layer in [
        ("route_direction_map", "direction_mapping", "silver"),
        ("stop_visits", "stop_visits", "silver"),
        ("headways", "headways", "silver"),
        ("route_dir_15m", "route_dir_15m", "gold"),
        ("stop_15m", "stop_15m", "gold"),
    ]:
        frame = namespace[function]().cache()
        result[table] = frame.count()
        tables[(layer, table)] = frame
    gold = tables[("gold", "route_dir_15m")].toPandas()
    assert gold.n_pings.sum() == len(pings)
    assert not gold.duplicated(
        ["route_short_name", "direction_id", "window_start", "data_origin"]
    ).any()
    assert len(gold) >= len(scheduled_grid(sched))
    shapes = build_shapes(gtfs["shapes"])
    patterns = build_patterns(gtfs["trips"], gtfs["stop_times"], gtfs["stops"], shapes)
    mapping = route_direction_map(pings, gtfs["routes"], gtfs["trips"], shapes)
    pings["service_date"] = pings.ts_gps.dt.tz_convert("America/Chicago").dt.date
    expected = pd.concat(
        [
            detect_stop_visits(group, mapping, shapes, patterns, sched)
            for _, group in pings.groupby(["vehicle_id", "service_date"])
        ]
    )
    actual = tables[("silver", "stop_visits")].toPandas()
    sort_keys = [
        "vehicle_id",
        "route_short_name",
        "direction_id",
        "stop_id",
        "arrive_ts",
    ]
    actual["arrive_ts"] = pd.to_datetime(actual.arrive_ts, utc=True)
    pd.testing.assert_frame_equal(
        actual[sort_keys].sort_values(sort_keys).reset_index(drop=True),
        expected[sort_keys].sort_values(sort_keys).reset_index(drop=True),
        check_dtype=False,
        check_exact=False,
        atol=1e-5,
    )
    assert result["stop_visits"] > 0
    print("SPARK CHECK PASSED:", result)
    spark.stop()


if __name__ == "__main__":
    run()
