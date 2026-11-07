"""Provenance-isolated headways and UTC quarter-hour operational aggregates."""

from __future__ import annotations

import numpy as np
import pandas as pd

HEADWAY_COLUMNS = [
    "stop_id",
    "route_short_name",
    "direction_id",
    "arrive_ts",
    "prev_arrive_ts",
    "obs_headway_s",
    "sched_headway_s",
    "headway_ratio",
    "is_bunched",
    "is_gap",
    "data_origin",
]
GOLD_COLUMNS = [
    "route_short_name",
    "direction_id",
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
]
ROUTE_KEYS = ["route_short_name", "direction_id", "window_start", "data_origin"]


def schedule_intervals(schedule):
    schedule = schedule.copy()
    schedule["sched_arrival_ts"] = pd.to_datetime(schedule.sched_arrival_ts, utc=True)
    schedule = schedule.drop_duplicates(
        ["stop_id", "route_short_name", "direction_id", "sched_arrival_ts"]
    )
    keys = ["stop_id", "route_short_name", "direction_id"]
    schedule = schedule.sort_values("sched_arrival_ts")
    schedule["sched_headway_s"] = (
        schedule.groupby(keys).sched_arrival_ts.diff().dt.total_seconds()
    )
    return schedule


def headways(visits, schedule):
    if visits.empty:
        return pd.DataFrame(columns=HEADWAY_COLUMNS)
    keys = ["stop_id", "route_short_name", "direction_id"]
    result = visits.copy().sort_values("arrive_ts")
    result["arrive_ts"] = pd.to_datetime(result.arrive_ts, utc=True)
    result["prev_arrive_ts"] = result.groupby(keys + ["data_origin"]).arrive_ts.shift()
    result["obs_headway_s"] = (
        result.arrive_ts - result.prev_arrive_ts
    ).dt.total_seconds()
    intervals = schedule_intervals(schedule)
    for key in keys:
        dtype = "int64" if key == "direction_id" else "object"
        result[key] = result[key].astype(dtype)
        intervals[key] = intervals[key].astype(dtype)
    result["arrive_ts"] = result.arrive_ts.astype("datetime64[ns, UTC]")
    intervals["sched_arrival_ts"] = intervals.sched_arrival_ts.astype(
        "datetime64[ns, UTC]"
    )
    result = pd.merge_asof(
        result.sort_values("arrive_ts"),
        intervals[keys + ["sched_arrival_ts", "sched_headway_s"]].sort_values(
            "sched_arrival_ts"
        ),
        left_on="arrive_ts",
        right_on="sched_arrival_ts",
        by=keys,
        direction="nearest",
        tolerance=pd.Timedelta(hours=1),
    )
    result["headway_ratio"] = result.obs_headway_s / result.sched_headway_s.where(
        result.sched_headway_s > 0
    )
    result["is_bunched"] = (result.headway_ratio < 0.5).astype("boolean")
    result["is_gap"] = (result.headway_ratio > 1.5).astype("boolean")
    result.loc[result.headway_ratio.isna(), ["is_bunched", "is_gap"]] = pd.NA
    return result[HEADWAY_COLUMNS]


def scheduled_grid(schedule):
    """Count distinct trips active in each window, from first to last stop.

    GTFS trip instances are keyed by service_date + trip_id, not trip_id alone.
    This prevents overcounting every stop as a scheduled trip.
    """
    if schedule.empty:
        return pd.DataFrame(columns=ROUTE_KEYS[:-1] + ["sched_trips"])
    schedule = schedule.copy()
    schedule["sched_arrival_ts"] = pd.to_datetime(schedule.sched_arrival_ts, utc=True)
    keys = ["route_short_name", "direction_id", "trip_id"]
    if "service_date" in schedule:
        keys += ["service_date"]
    spans = schedule.groupby(keys).sched_arrival_ts.agg(["min", "max"]).reset_index()
    spans["window_start"] = [
        pd.date_range(a.floor("15min"), b.floor("15min"), freq="15min")
        for a, b in zip(spans["min"], spans["max"])
    ]
    grid = spans.explode("window_start")
    return grid.groupby(ROUTE_KEYS[:-1]).size().rename("sched_trips").reset_index()


def map_pings(pings, mapping):
    if pings.empty:
        return pings.assign(direction_id=pd.Series(dtype="int64"))
    selected = mapping[mapping.is_selected]
    return pings.merge(
        selected[["route_short_name", "direction_key", "direction_id", "data_origin"]],
        on=["route_short_name", "direction_key", "data_origin"],
        how="inner",
    )


def route_dir_15m(pings, mapping, schedule, visits, hw):
    pings = map_pings(pings, mapping).copy()
    pings["ts_gps"] = pd.to_datetime(pings.ts_gps, utc=True)
    pings["window_start"] = pings.ts_gps.dt.floor("15min")
    measured = (
        pings.groupby(ROUTE_KEYS)
        .agg(
            avg_load_factor=("load_factor", "mean"),
            p90_load_factor=("load_factor", lambda x: x.quantile(0.9)),
            max_load_factor=("load_factor", "max"),
            active_vehicles=("vehicle_id", "nunique"),
            n_pings=("vehicle_id", "size"),
            direction_name=("direction_name", "first"),
        )
        .reset_index()
    )
    grid = scheduled_grid(schedule)
    origins = sorted(set(pings.data_origin) | {"observed"})
    grid = grid.merge(pd.DataFrame({"data_origin": origins}), how="cross")
    result = grid.merge(measured, on=ROUTE_KEYS, how="outer")
    result["sched_trips"] = result.sched_trips.fillna(0).astype(int)
    result["n_pings"] = result.n_pings.fillna(0).astype(int)
    result["service_ratio"] = result.active_vehicles / result.sched_trips.where(
        result.sched_trips > 0
    )
    # Names may be filled from other observations, but never borrowed across origins.
    names = pings.groupby(
        ["route_short_name", "direction_id", "data_origin"]
    ).direction_name.first()
    result["direction_name"] = result.direction_name.fillna(
        pd.Series(
            [
                names.get((r.route_short_name, r.direction_id, r.data_origin))
                for r in result.itertuples()
            ],
            index=result.index,
        )
    )
    if len(hw):
        hw = hw.copy()
        hw["window_start"] = pd.to_datetime(hw.arrive_ts, utc=True).dt.floor("15min")
        metrics = (
            hw.groupby(ROUTE_KEYS)
            .agg(
                headway_cv=(
                    "obs_headway_s",
                    lambda x: x.std(ddof=1) / x.mean() if x.mean() > 0 else np.nan,
                ),
                bunching_rate=("is_bunched", "mean"),
            )
            .reset_index()
        )
        result = result.merge(metrics, on=ROUTE_KEYS, how="left")
    else:
        result[["headway_cv", "bunching_rate"]] = np.nan
    if len(visits):
        visits = visits.copy()
        visits["window_start"] = pd.to_datetime(visits.arrive_ts, utc=True).dt.floor(
            "15min"
        )
        delay = (
            visits.groupby(ROUTE_KEYS)
            .delay_s.mean()
            .rename("mean_delay_s")
            .reset_index()
        )
        result = result.merge(delay, on=ROUTE_KEYS, how="left")
    else:
        result["mean_delay_s"] = np.nan
    return result[GOLD_COLUMNS].sort_values(ROUTE_KEYS).reset_index(drop=True)


def stop_15m(visits, hw, pings):
    keys = ["stop_id", "window_start", "data_origin"]
    v = visits.copy()
    v["window_start"] = pd.to_datetime(v.arrive_ts, utc=True).dt.floor("15min")
    counts = v.groupby(keys).agg(
        arrivals=("vehicle_id", "size"),
        net_board_proxy_sum=("net_board_proxy", lambda x: x.sum(min_count=1)),
    )
    h = hw.copy()
    h["window_start"] = pd.to_datetime(h.arrive_ts, utc=True).dt.floor("15min")
    ratios = h.groupby(keys).headway_ratio.mean().rename("mean_headway_ratio")
    # Passing load means load factor, measured only within 30 m of a stop.
    passing = pings[pings.nearest_stop_dist_m <= 30].copy()
    passing["stop_id"] = passing.nearest_stop_id
    passing["window_start"] = pd.to_datetime(passing.ts_gps, utc=True).dt.floor("15min")
    loads = passing.groupby(keys).load_factor.max().rename("max_passing_load")
    return (
        counts.join(ratios, how="outer")
        .join(loads, how="outer")
        .reset_index()[
            keys[:2]
            + [
                "arrivals",
                "mean_headway_ratio",
                "net_board_proxy_sum",
                "max_passing_load",
                "data_origin",
            ]
        ]
    )
