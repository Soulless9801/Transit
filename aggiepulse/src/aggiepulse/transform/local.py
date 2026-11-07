"""Run the operations pipeline on landed data: uv run python -m aggiepulse.transform.local."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

from aggiepulse.collector.parse import flatten_vehicles
from aggiepulse.common.paths import data_root
from aggiepulse.ingest.gtfs import latest_gtfs_zip, load_gtfs

from .aggregates import headways, route_dir_15m, stop_15m
from .geometry import EARTH_RADIUS_M, build_shapes
from .operations import build_patterns, detect_stop_visits, route_direction_map


def expand_schedule(gtfs, service_dates):
    """Local-only expansion; production consumes core.schedule_expanded.

    GTFS clock is elapsed seconds from local noon minus 12 hours, per the GTFS
    definition. UTC arithmetic therefore stays monotonic across DST transitions.
    """
    dates = gtfs["calendar_dates"].copy()
    dates["service_date"] = pd.to_datetime(dates.date, format="%Y%m%d").dt.date
    dates = dates[
        (pd.to_numeric(dates.exception_type) == 1)
        & dates.service_date.isin(service_dates)
    ]
    trips = gtfs["trips"].merge(
        gtfs["routes"][["route_id", "route_short_name"]], on="route_id"
    )
    schedule = (
        dates[["service_id", "service_date"]]
        .merge(trips, on="service_id")
        .merge(gtfs["stop_times"], on="trip_id")
    )
    for source, target in [
        ("arrival_time", "sched_arrival_ts"),
        ("departure_time", "sched_departure_ts"),
    ]:
        noon = pd.DatetimeIndex(
            pd.to_datetime(schedule.service_date.astype(str)) + pd.Timedelta(hours=12)
        ).tz_localize("America/Chicago")
        seconds = (
            schedule[source]
            .str.split(":")
            .map(lambda x: int(x[0]) * 3600 + int(x[1]) * 60 + int(x[2]))
        )
        schedule[target] = (
            noon.tz_convert("UTC")
            - pd.Timedelta(hours=12)
            + pd.to_timedelta(seconds.to_numpy(), unit="s")
        )
    schedule["direction_id"] = pd.to_numeric(schedule.direction_id).astype(int)
    schedule["stop_sequence"] = pd.to_numeric(schedule.stop_sequence).astype(int)
    schedule["is_timepoint"] = schedule.timepoint == "1"
    return schedule


def load_local_pings():
    landing = data_root() / "landing"
    base_files = sorted((landing / "tripspark_base").rglob("*.json.gz"))
    with gzip.open(base_files[-1], "rt") as handle:
        base = json.load(handle)["payload"]
    rows = []
    for file in sorted((landing / "tripspark_vehicles").rglob("*.jsonl.gz")):
        try:
            with gzip.open(file, "rt") as handle:
                for line in handle:
                    record = json.loads(line)
                    if record.get("http_status") != 200:
                        continue
                    for vehicle in flatten_vehicles(
                        record["payload"], record["ingest_ts"], base
                    ):
                        rows.append(dict(vehicle, ingest_ts=record["ingest_ts"]))
        except (EOFError, json.JSONDecodeError):
            # Collector may still be appending the last gzip member.
            continue
    pings = pd.DataFrame(rows).rename(columns={"ts_gps_utc": "ts_gps"})
    for col in ["ts_gps", "ingest_ts"]:
        pings[col] = pd.to_datetime(pings[col], utc=True)
    age = (pings.ingest_ts - pings.ts_gps).dt.total_seconds()
    pings = pings[
        pings.lat.between(30.4, 30.9)
        & pings.lon.between(-96.6, -96.1)
        & age.between(-60, 300)
        & pings.onboard.between(0, 1.5 * pings.capacity)
    ]
    return (
        pings.dropna(subset=["vehicle_id", "ts_gps", "lat", "lon"])
        .drop_duplicates(["vehicle_id", "ts_gps"])
        .assign(data_origin="observed")
    )


def run():
    gtfs = load_gtfs(latest_gtfs_zip())
    pings = load_local_pings()
    shapes = build_shapes(gtfs["shapes"])
    patterns = build_patterns(gtfs["trips"], gtfs["stop_times"], gtfs["stops"], shapes)
    mapping = route_direction_map(pings, gtfs["routes"], gtfs["trips"], shapes)
    pings["service_date"] = pings.ts_gps.dt.tz_convert("America/Chicago").dt.date
    dates = set(pings.service_date)
    schedule = expand_schedule(gtfs, dates | {d - pd.Timedelta(days=1) for d in dates})
    visits = pd.concat(
        [
            detect_stop_visits(group, mapping, shapes, patterns, schedule)
            for _, group in pings.groupby(["vehicle_id", "service_date"])
        ],
        ignore_index=True,
    )
    hw = headways(visits, schedule)
    # Nearest stop is supplied by core in production; compute it locally.
    stop_xy = gtfs["stops"][["stop_lat", "stop_lon"]].astype(float).to_numpy()
    ping_xy = pings[["lat", "lon"]].to_numpy()
    delta = np.deg2rad(ping_xy[:, None, :] - stop_xy[None, :, :])
    delta[:, :, 1] *= np.cos(np.deg2rad(30.6))
    dist = np.linalg.norm(delta, axis=2) * EARTH_RADIUS_M
    nearest = dist.argmin(axis=1)
    pings["nearest_stop_id"] = gtfs["stops"].stop_id.to_numpy()[nearest]
    pings["nearest_stop_dist_m"] = dist[np.arange(len(dist)), nearest]
    gold = route_dir_15m(pings, mapping, schedule, visits, hw)
    stops = stop_15m(visits, hw, pings)
    proxy = visits.groupby("route_short_name").net_board_proxy.agg(
        ["count", "sum", "min", "max"]
    )
    summary = {
        "n_pings": len(pings),
        "vehicles": pings.vehicle_id.nunique(),
        "start_utc": str(pings.ts_gps.min()),
        "end_utc": str(pings.ts_gps.max()),
        "mapping": mapping[mapping.is_selected].to_dict("records"),
        "stop_visits": len(visits),
        "visits_per_route": visits.groupby("route_short_name").size().to_dict(),
        "headway_seconds": hw.obs_headway_s.describe(
            percentiles=[0.1, 0.5, 0.9, 0.99]
        ).to_dict(),
        "net_board_proxy": proxy.to_dict("index"),
        "matched_visits": int(visits.matched_trip_id.notna().sum()),
        "gold_route_rows": len(gold),
        "schedule_only_rows": int((gold.n_pings == 0).sum()),
        "gold_stop_rows": len(stops),
        "proxy_sanity": {
            "negative_counts": int((visits.net_board_proxy < 0).sum()),
            "positive_counts": int((visits.net_board_proxy > 0).sum()),
            "abs_delta_over_capacity": int(
                (visits.net_board_proxy.abs() > pings.capacity.max()).sum()
            ),
        },
    }
    output = (
        Path(__file__).resolve().parents[3] / "pipelines" / "ops" / "local_summary.json"
    )
    summary["headway_seconds"] = {
        key: None if pd.isna(value) else value
        for key, value in summary["headway_seconds"].items()
    }
    output.write_text(
        json.dumps(summary, indent=2, default=str, allow_nan=False) + "\n"
    )
    print(json.dumps(summary, indent=2, default=str))
    return summary


if __name__ == "__main__":
    run()
