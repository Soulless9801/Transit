"""Route fitting, ordered stop crossings and conservative scheduled-trip matching."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .geometry import Shape

VISIT_COLUMNS = [
    "vehicle_id",
    "route_short_name",
    "direction_id",
    "direction_name",
    "stop_id",
    "stop_sequence",
    "arrive_ts",
    "depart_ts",
    "dwell_s",
    "onboard_before",
    "onboard_after",
    "net_board_proxy",
    "shape_dist_m",
    "matched_trip_id",
    "sched_ts",
    "delay_s",
    "data_origin",
]
MAP_COLUMNS = [
    "route_short_name",
    "direction_key",
    "route_id",
    "direction_id",
    "shape_id",
    "median_offset_m",
    "fit_quality",
    "backward_fraction",
    "candidate_rank",
    "is_selected",
    "n_pings",
    "data_origin",
]


def route_direction_map(pings, routes, trips, shapes: dict[str, Shape]):
    """Rank every candidate pattern; only an on-route best candidate is selected.

    Median offset is primary. The fraction of directed backwards displacements
    breaks ties on shared/reversed geometry. Quality is exp(-median/50), not a
    calibrated probability. A rank is deterministic even for exact ties.
    """
    rows = []
    patterns = trips[["route_id", "direction_id", "shape_id"]].drop_duplicates()
    patterns = patterns.merge(routes[["route_id", "route_short_name"]], on="route_id")
    for (short, key, origin), group in pings.groupby(
        ["route_short_name", "direction_key", "data_origin"], dropna=False
    ):
        candidates = []
        # Sampling is bounded, time ordered per vehicle for the direction tie-break.
        sample = group.sort_values(["vehicle_id", "ts_gps"]).iloc[
            :: max(1, len(group) // 2000)
        ]
        for pattern in patterns[patterns.route_short_name == short].itertuples():
            shape = shapes.get(str(pattern.shape_id))
            if shape is None:
                continue
            projected = shape.project(sample, monotonic=False)
            median = float(projected.offset_m.median())
            backwards = []
            for _, vehicle in projected.groupby("vehicle_id"):
                delta = vehicle.shape_dist_m.diff()
                if shape.loop:
                    delta = (delta + shape.length / 2) % shape.length - shape.length / 2
                moving = delta.abs() > 20
                backwards.extend((delta[moving] < 0).tolist())
            reverse = float(np.mean(backwards)) if backwards else 0.0
            candidates.append(
                {
                    "route_short_name": short,
                    "direction_key": key,
                    "route_id": str(pattern.route_id),
                    "direction_id": int(pattern.direction_id),
                    "shape_id": str(pattern.shape_id),
                    "median_offset_m": median,
                    "fit_quality": float(np.exp(-median / 50)),
                    "backward_fraction": reverse,
                    "n_pings": len(group),
                    "data_origin": origin,
                }
            )
        candidates.sort(
            key=lambda x: (
                round(x["median_offset_m"] / 5),
                x["backward_fraction"],
                x["median_offset_m"],
                x["shape_id"],
            )
        )
        for rank, candidate in enumerate(candidates, 1):
            candidate.update(
                candidate_rank=rank,
                is_selected=rank == 1 and candidate["median_offset_m"] <= 100,
            )
            rows.append(candidate)
    return pd.DataFrame(rows, columns=MAP_COLUMNS)


def build_patterns(trips, stop_times, stops, shapes):
    """Build the longest distinct stop pattern for each shape.

    GTFS distances are in feed-defined units: interpolate their scale against
    shape vertices, rather than assuming metres. Otherwise project in stop order.
    """
    result = {}
    times = stop_times.copy()
    times["stop_sequence"] = pd.to_numeric(times.stop_sequence)
    times = times.merge(stops[["stop_id", "stop_lat", "stop_lon"]], on="stop_id")
    for shape_id, shape_trips in trips.groupby("shape_id"):
        shape = shapes[str(shape_id)]
        candidate_patterns = []
        joined = times[times.trip_id.isin(shape_trips.trip_id)]
        for _, pattern in joined.groupby("trip_id", sort=True):
            pattern = pattern.sort_values("stop_sequence").copy()
            signature = tuple(zip(pattern.stop_id, pattern.stop_sequence))
            if any(signature == old[0] for old in candidate_patterns):
                continue
            previous = 0.0
            distances = []
            for row in pattern.itertuples():
                source = pd.to_numeric(
                    getattr(row, "shape_dist_traveled", None), errors="coerce"
                )
                if pd.notna(source) and shape.source_distance is not None:
                    distance = float(
                        np.interp(source, shape.source_distance, shape.cumulative)
                    )
                else:
                    d, off = shape.candidates(float(row.stop_lat), float(row.stop_lon))
                    off[d < previous - 15] = np.inf
                    if not np.isfinite(off).any():
                        distance = np.nan
                    else:
                        distance = float(d[np.argmin(off)])
                distances.append(distance)
                if np.isfinite(distance):
                    previous = distance
            pattern["shape_dist_m"] = distances
            candidate_patterns.append(
                (signature, pattern[["stop_id", "stop_sequence", "shape_dist_m"]])
            )
        # Keep the complete pattern. GPS alone cannot distinguish a skipped
        # stop from a short-turn trip that shares this shape.
        result[str(shape_id)] = max((p for _, p in candidate_patterns), key=len)
    return result


def _match_trips(visits, schedule, *, max_delay_s=900.0, ambiguity_margin_s=60.0):
    """One trip per observed pass; require ordered evidence and a clear winner."""
    if visits.empty or schedule is None or schedule.empty:
        return visits
    schedule = schedule.copy()
    schedule["sched_arrival_ts"] = pd.to_datetime(schedule.sched_arrival_ts, utc=True)
    for _, pass_visits in visits.groupby("_pass_id"):
        short = pass_visits.route_short_name.iloc[0]
        direction = pass_visits.direction_id.iloc[0]
        relevant = schedule[
            (schedule.route_short_name == short)
            & (pd.to_numeric(schedule.direction_id) == direction)
        ]
        # Service dates are explicit, including trips with GTFS times >24:00.
        if "service_date" in relevant:
            dates = pass_visits.arrive_ts.dt.tz_convert("America/Chicago").dt.date
            allowed = set(dates) | {d - pd.Timedelta(days=1) for d in dates}
            relevant = relevant[
                pd.to_datetime(relevant.service_date).dt.date.isin(allowed)
            ]
        scores = []
        trip_keys = (
            ["trip_id", "service_date"] if "service_date" in relevant else ["trip_id"]
        )
        for instance, trip in relevant.groupby(trip_keys):
            trip_id = instance[0] if isinstance(instance, tuple) else instance
            matched = pass_visits.merge(
                trip, on=["stop_id", "stop_sequence"], suffixes=("", "_s")
            )
            if matched.empty:
                continue
            matched = matched.sort_values("arrive_ts")
            if (matched.stop_sequence.diff().dropna() < 0).any():
                continue
            timepoints = matched
            if "is_timepoint" in matched:
                timepoints = matched[
                    matched.is_timepoint.fillna(False)
                    .astype(str)
                    .isin(["True", "1", "true"])
                ]
            # One stop is insufficient to disambiguate headway-style operations.
            if len(timepoints) < 2:
                continue
            delays = (
                timepoints.arrive_ts - timepoints.sched_arrival_ts
            ).dt.total_seconds()
            if abs(delays.median()) > max_delay_s or delays.std(ddof=0) > 180:
                continue
            scores.append((float(delays.abs().median()), str(trip_id), trip))
        scores.sort(key=lambda x: x[0])
        if not scores or (
            len(scores) > 1 and scores[1][0] - scores[0][0] < ambiguity_margin_s
        ):
            continue
        _, trip_id, trip = scores[0]
        for index, visit in pass_visits.iterrows():
            stop = trip[
                (trip.stop_id == visit.stop_id)
                & (trip.stop_sequence == visit.stop_sequence)
            ]
            if not stop.empty:
                scheduled = stop.sched_arrival_ts.iloc[0]
                visits.loc[index, ["matched_trip_id", "sched_ts", "delay_s"]] = [
                    trip_id,
                    scheduled,
                    (visit.arrive_ts - scheduled).total_seconds(),
                ]
    return visits


def detect_stop_visits(pings, mapping, shapes, patterns, schedule=None):
    """Run on one vehicle/service-day, splitting route changes and GPS outages.

    Crossings require <=180 s between accepted pings, <=100 m offsets and
    plausible speed. Dwell uses stationary observations (vendor speed <=0.5)
    within 30 m along the pattern AND 30 m perpendicular distance. No dwell
    is inferred from undocumented nonzero speed units.
    """
    if pings.empty:
        return pd.DataFrame(columns=VISIT_COLUMNS)
    pings = pings.copy()
    pings["ts_gps"] = pd.to_datetime(pings.ts_gps, utc=True)
    for column in ["onboard", "speed", "lat", "lon"]:
        pings[column] = pd.to_numeric(pings[column], errors="coerce")
    pings = pings.sort_values("ts_gps").drop_duplicates(["vehicle_id", "ts_gps"])
    # A return to a route later in the day is a new contiguous run.
    keys = ["route_short_name", "direction_key", "data_origin"]
    pings["_run"] = pings[keys].ne(pings[keys].shift()).any(axis=1).cumsum()
    rows = []
    for run_id, run in pings.groupby("_run"):
        chosen = mapping[
            (mapping.route_short_name == run.route_short_name.iloc[0])
            & (mapping.direction_key == run.direction_key.iloc[0])
            & (mapping.data_origin == run.data_origin.iloc[0])
            & mapping.is_selected
        ]
        if chosen.empty:
            continue
        choice = chosen.iloc[0]
        alternatives = mapping[
            (mapping.route_short_name == run.route_short_name.iloc[0])
            & (mapping.direction_key == run.direction_key.iloc[0])
            & (mapping.data_origin == run.data_origin.iloc[0])
            & (mapping.direction_id == choice.direction_id)
        ]
        if len(alternatives) > 1:
            # Route-wide rank selects the direction; each contiguous vehicle run
            # may use a different branch/shape within that direction.
            scores = [
                (
                    shapes[str(candidate.shape_id)]
                    .project(run, monotonic=False)
                    .offset_m.median(),
                    candidate,
                )
                for _, candidate in alternatives.iterrows()
            ]
            choice = min(scores, key=lambda value: value[0])[1]
        shape = shapes[str(choice.shape_id)]
        projected = shape.project(run)
        pattern = patterns[str(choice.shape_id)]
        for segment_id, segment in projected.groupby("segment_id"):
            segment = segment.dropna(subset=["progress_m"])
            for left, right in zip(
                segment.iloc[:-1].itertuples(), segment.iloc[1:].itertuples()
            ):
                dt = (right.ts_gps - left.ts_gps).total_seconds()
                if dt <= 0 or dt > 180 or right.progress_m <= left.progress_m:
                    continue
                first_lap = (
                    int(np.floor(left.progress_m / shape.length)) if shape.loop else 0
                )
                last_lap = (
                    int(np.floor(right.progress_m / shape.length)) if shape.loop else 0
                )
                for lap in range(first_lap, last_lap + 1):
                    for stop in pattern.itertuples():
                        stop_distance = stop.shape_dist_m
                        if abs(stop_distance - shape.length) < 1:
                            stop_distance = shape.length
                        elif abs(stop_distance) < 1:
                            stop_distance = 0.0
                        target = stop_distance + lap * shape.length
                        if not (left.progress_m < target <= right.progress_m):
                            continue
                        fraction = (target - left.progress_m) / (
                            right.progress_m - left.progress_m
                        )
                        crossing = left.ts_gps + pd.Timedelta(
                            microseconds=round(fraction * dt * 1_000_000)
                        )
                        nearby = segment[
                            (segment.progress_m.sub(target).abs() <= 30)
                            & (segment.offset_m <= 30)
                            & (pd.to_numeric(segment.speed, errors="coerce") <= 0.5)
                            & (
                                segment.ts_gps.sub(crossing).abs()
                                <= pd.Timedelta(minutes=3)
                            )
                        ]
                        arrive = (
                            min(crossing, nearby.ts_gps.min())
                            if len(nearby)
                            else crossing
                        )
                        depart = (
                            max(crossing, nearby.ts_gps.max())
                            if len(nearby)
                            else crossing
                        )
                        # Use pings outside the whole dwell to avoid double counting
                        # the same APC change on arrival and departure.
                        before = segment[segment.ts_gps <= arrive]
                        after = segment[segment.ts_gps >= depart]
                        onboard_before = (
                            before.onboard.iloc[-1] if len(before) else left.onboard
                        )
                        onboard_after = (
                            after.onboard.iloc[0] if len(after) else right.onboard
                        )
                        rows.append(
                            {
                                "vehicle_id": str(left.vehicle_id),
                                "route_short_name": str(left.route_short_name),
                                "direction_id": int(choice.direction_id),
                                "direction_name": getattr(left, "direction_name", None),
                                "stop_id": str(stop.stop_id),
                                "stop_sequence": int(stop.stop_sequence),
                                "arrive_ts": arrive,
                                "depart_ts": depart,
                                "dwell_s": (depart - arrive).total_seconds(),
                                "onboard_before": onboard_before,
                                "onboard_after": onboard_after,
                                "net_board_proxy": onboard_after - onboard_before,
                                "shape_dist_m": float(stop.shape_dist_m),
                                "matched_trip_id": None,
                                "sched_ts": pd.NaT,
                                "delay_s": np.nan,
                                "data_origin": left.data_origin,
                                "_pass_id": f"{run_id}:{segment_id}:{lap}",
                            }
                        )
    visits = pd.DataFrame(rows)
    if visits.empty:
        return pd.DataFrame(columns=VISIT_COLUMNS)
    visits["sched_ts"] = pd.to_datetime(visits.sched_ts, utc=True)
    # Loop endpoints can represent the same physical terminal at sequence 0/N.
    # Keep the stop at the end of the completed pass, once per crossing.
    visits = visits.sort_values(["arrive_ts", "stop_sequence"], ascending=[True, False])
    visits = visits.drop_duplicates(
        ["vehicle_id", "stop_id", "arrive_ts", "data_origin"]
    )
    visits = _match_trips(visits, schedule)
    return visits[VISIT_COLUMNS].sort_values("arrive_ts").reset_index(drop=True)
