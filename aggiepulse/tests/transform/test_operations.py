import numpy as np
import pandas as pd
import pytest

from aggiepulse.transform import (
    Shape,
    build_patterns,
    detect_stop_visits,
    headways,
    route_dir_15m,
    route_direction_map,
)
from aggiepulse.transform.aggregates import GOLD_COLUMNS
from aggiepulse.transform.operations import VISIT_COLUMNS


def shape(points, loop=False):
    # Metres -> longitude near the equator, making expected distances transparent.
    frame = pd.DataFrame(
        {
            "shape_pt_lat": [y / 111195.08 for x, y in points],
            "shape_pt_lon": [x / 111195.08 for x, y in points],
            "shape_pt_sequence": range(len(points)),
        }
    )
    return Shape(frame)


def pings(distances, times=None, onboard=None):
    return pd.DataFrame(
        {
            "vehicle_id": "v",
            "route_short_name": "01",
            "direction_key": "d",
            "direction_name": "Out",
            "data_origin": "observed",
            "lat": 0.0,
            "lon": np.array(distances) / 111195.08,
            "speed": 5.0,
            "onboard": onboard or [5] * len(distances),
            "load_factor": 0.1,
            "ts_gps": pd.to_datetime(times, utc=True)
            if times
            else pd.date_range(
                "2026-11-01T06:59:30Z", periods=len(distances), freq="30s"
            ),
        }
    )


def mapping():
    return pd.DataFrame(
        [
            {
                "route_short_name": "01",
                "direction_key": "d",
                "direction_id": 0,
                "shape_id": "s",
                "is_selected": True,
                "data_origin": "observed",
            }
        ]
    )


def schedule():
    return pd.DataFrame(
        {
            "service_date": ["2026-11-01"] * 4,
            "trip_id": ["a", "a", "b", "b"],
            "route_short_name": "01",
            "direction_id": 0,
            "stop_id": ["x", "y"] * 2,
            "stop_sequence": [0, 1] * 2,
            "is_timepoint": True,
            "sched_arrival_ts": pd.to_datetime(
                [
                    "2026-11-01T06:59:00Z",
                    "2026-11-01T07:16:00Z",
                    "2026-11-01T07:29:00Z",
                    "2026-11-01T07:46:00Z",
                ]
            ),
        }
    )


def test_projection_straight_offset_and_backwards_rejection():
    s = shape([(0, 0), (1000, 0)])
    p = pings([100, 200, 110, 300])
    p["lat"] = 10 / 111195.08
    result = s.project(p)
    np.testing.assert_allclose(result.offset_m, 10, atol=0.01)
    np.testing.assert_allclose(
        result.shape_dist_m.iloc[[0, 1, 3]], [100, 200, 300], atol=0.01
    )
    assert np.isnan(result.progress_m.iloc[2])


def test_loop_wrap_and_shared_endpoint():
    s = shape([(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)])
    p = pings([0, 0, 10, 50])
    p["lat"] = np.array([50, 10, 0, 0]) / 111195.08
    result = s.project(p)
    assert s.loop
    np.testing.assert_allclose(result.progress_m, [350, 390, 410, 450], atol=0.1)


def test_crossing_interpolation_dst_and_onboard():
    p = pings([0, 100], onboard=[5, 8])
    pattern = pd.DataFrame(
        {"stop_id": ["x"], "stop_sequence": [0], "shape_dist_m": [50.0]}
    )
    visits = detect_stop_visits(
        p, mapping(), {"s": shape([(0, 0), (100, 0)])}, {"s": pattern}
    )
    assert len(visits) == 1
    assert visits.arrive_ts.iloc[0] == pd.Timestamp("2026-11-01T06:59:45Z")
    assert visits.net_board_proxy.iloc[0] == 3
    # America/Chicago repeats 01:00; UTC elapsed time remains 30 seconds.
    assert p.ts_gps.diff().iloc[1].total_seconds() == 30
    assert visits.dwell_s.iloc[0] == 0


def test_dwell_and_outage_do_not_create_repeated_visits():
    p = pings([0, 48, 50, 52, 100], onboard=[5, 5, 7, 8, 8])
    p.loc[1:3, "speed"] = 0.0
    pattern = pd.DataFrame(
        {"stop_id": ["x"], "stop_sequence": [0], "shape_dist_m": [50.0]}
    )
    visits = detect_stop_visits(
        p, mapping(), {"s": shape([(0, 0), (100, 0)])}, {"s": pattern}
    )
    assert len(visits) == 1
    assert visits.dwell_s.iloc[0] == 60
    assert visits.net_board_proxy.iloc[0] == 3
    p.loc[1:, "ts_gps"] += pd.Timedelta(hours=1)
    assert detect_stop_visits(
        p.iloc[[0, -1]], mapping(), {"s": shape([(0, 0), (100, 0)])}, {"s": pattern}
    ).empty


def test_headway_flags_and_provenance():
    sched = schedule()
    sched = pd.concat(
        [
            sched,
            sched.assign(
                sched_arrival_ts=sched.sched_arrival_ts - pd.Timedelta(hours=1)
            ),
        ]
    )
    visits = pd.DataFrame(
        {
            "stop_id": "x",
            "route_short_name": "01",
            "direction_id": 0,
            "arrive_ts": pd.to_datetime(
                [
                    "2026-11-01T06:59Z",
                    "2026-11-01T07:04Z",
                    "2026-11-01T08:00Z",
                    "2026-11-01T07:05Z",
                ]
            ),
            "data_origin": ["observed"] * 3 + ["simulated"],
        }
    )
    h = headways(visits, sched)
    observed = h[h.data_origin == "observed"]
    assert pd.isna(observed.is_bunched.iloc[0])
    assert observed.is_bunched.iloc[1]
    assert observed.is_gap.iloc[2]
    assert h[h.data_origin == "simulated"].obs_headway_s.isna().all()


def test_gold_full_scheduled_grid_and_no_stop_double_count():
    p = pings([0, 100])
    v = pd.DataFrame(columns=VISIT_COLUMNS)
    h = headways(v, schedule())
    g = route_dir_15m(p, mapping(), schedule(), v, h)
    assert list(g.columns) == GOLD_COLUMNS
    # Two trip spans: 06:45..07:15 and 07:15..07:45 inclusive.
    assert len(g) == 5
    assert g.n_pings.sum() == 2
    assert (g.n_pings == 0).sum() == 3
    assert (
        g[g.window_start == pd.Timestamp("2026-11-01T07:15Z")].sched_trips.iloc[0] == 2
    )
    assert g.window_start.dt.minute.isin([0, 15, 30, 45]).all()
    assert g[g.n_pings == 0].avg_load_factor.isna().all()


def test_direction_mapping_ranks_multiple_shapes_by_fit_and_order():
    routes = pd.DataFrame({"route_id": ["r"], "route_short_name": ["01"]})
    trips = pd.DataFrame(
        {
            "route_id": ["r"] * 3,
            "direction_id": [0, 1, 0],
            "shape_id": ["s", "reverse", "far"],
        }
    )
    shapes = {
        "s": shape([(0, 0), (1000, 0)]),
        "reverse": shape([(1000, 0), (0, 0)]),
        "far": shape([(0, 500), (1000, 500)]),
    }
    result = route_direction_map(pings([100, 200, 300]), routes, trips, shapes)
    assert result[result.is_selected].shape_id.iloc[0] == "s"
    assert len(result) == 3
    assert result.iloc[0].fit_quality == pytest.approx(1)


def test_stop_distance_feed_units_are_scaled():
    points = pd.DataFrame(
        {
            "shape_pt_lat": [0.0, 0.0],
            "shape_pt_lon": [0.0, 1000 / 111195.08],
            "shape_pt_sequence": [0, 1],
            "shape_dist_traveled": [0.0, 1.0],
        }
    )
    trips = pd.DataFrame({"trip_id": ["t"], "shape_id": ["s"]})
    times = pd.DataFrame(
        {
            "trip_id": ["t"],
            "stop_id": ["x"],
            "stop_sequence": [0],
            "shape_dist_traveled": [0.5],
        }
    )
    stops = pd.DataFrame(
        {"stop_id": ["x"], "stop_lat": [0.0], "stop_lon": [500 / 111195.08]}
    )
    pattern = build_patterns(trips, times, stops, {"s": Shape(points)})["s"]
    assert pattern.shape_dist_m.iloc[0] == pytest.approx(500, abs=0.01)


def test_trip_matching_requires_ordered_timepoints_and_clear_winner():
    from aggiepulse.transform.operations import _match_trips

    v = pd.DataFrame(
        {
            "_pass_id": ["p", "p"],
            "route_short_name": "01",
            "direction_id": 0,
            "stop_id": ["x", "y"],
            "stop_sequence": [0, 1],
            "arrive_ts": pd.to_datetime(
                ["2026-11-01T06:59:10Z", "2026-11-01T07:16:10Z"]
            ),
            "matched_trip_id": None,
            "sched_ts": pd.Series([pd.NaT] * 2, dtype="datetime64[ns, UTC]"),
            "delay_s": np.nan,
        }
    )
    matched = _match_trips(v.copy(), schedule())
    assert matched.matched_trip_id.tolist() == ["a", "a"]
    assert matched.delay_s.tolist() == [10.0, 10.0]
    near = schedule().query("trip_id == 'a'").assign(trip_id="ambiguous")
    near["sched_arrival_ts"] += pd.Timedelta(seconds=20)
    ambiguous = _match_trips(v.copy(), pd.concat([schedule(), near]))
    assert ambiguous.matched_trip_id.isna().all()
    unordered = v.iloc[::-1].copy()
    unordered["arrive_ts"] = sorted(v.arrive_ts)
    assert _match_trips(unordered, schedule()).matched_trip_id.isna().all()


def test_loop_crossings_at_terminal_are_deduplicated():
    s = shape([(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)])
    p = pings([0, 0, 10, 50])
    p["lat"] = np.array([50, 10, 0, 0]) / 111195.08
    pattern = pd.DataFrame(
        {
            "stop_id": ["terminal", "terminal", "next"],
            "stop_sequence": [0, 2, 1],
            "shape_dist_m": [0.0, 400.0, 25.0],
        }
    )
    v = detect_stop_visits(p, mapping(), {"s": s}, {"s": pattern})
    assert v.stop_id.tolist() == ["terminal", "next"]
    assert v.arrive_ts.is_monotonic_increasing


def test_alternate_shape_per_vehicle_run():
    shapes = {"s": shape([(0, 0), (1000, 0)]), "branch": shape([(0, 500), (1000, 500)])}
    m = pd.concat([mapping(), mapping().assign(shape_id="branch", is_selected=False)])
    p = pings([0, 100])
    p["lat"] = 500 / 111195.08
    pattern = pd.DataFrame(
        {"stop_id": ["x"], "stop_sequence": [0], "shape_dist_m": [50.0]}
    )
    v = detect_stop_visits(
        p, m, shapes, {"s": pattern.assign(stop_id="wrong"), "branch": pattern}
    )
    assert v.stop_id.tolist() == ["x"]


def test_empty_observations_still_emit_schedule():
    p = pings([0]).iloc[:0]
    v = pd.DataFrame(columns=VISIT_COLUMNS)
    g = route_dir_15m(p, mapping(), schedule(), v, headways(v, schedule()))
    assert len(g) == 5
    assert (g.n_pings == 0).all()
    assert g.active_vehicles.isna().all()


def test_simulated_and_observed_aggregates_never_mix():
    p = pd.concat(
        [
            pings([0, 100]),
            pings([0, 100]).assign(data_origin="simulated", load_factor=0.9),
        ]
    )
    m = pd.concat([mapping(), mapping().assign(data_origin="simulated")])
    v = pd.DataFrame(columns=VISIT_COLUMNS)
    g = route_dir_15m(p, m, schedule(), v, headways(v, schedule()))
    assert g[g.n_pings > 0].groupby(
        "data_origin"
    ).avg_load_factor.first().to_dict() == {"observed": 0.1, "simulated": 0.9}
    assert len(g) == 10


def test_dst_fold_uses_utc_elapsed_time():
    p = pings(
        [0, 100], times=["2026-11-01T01:59:45-05:00", "2026-11-01T01:00:15-06:00"]
    )
    pattern = pd.DataFrame(
        {"stop_id": ["x"], "stop_sequence": [0], "shape_dist_m": [50.0]}
    )
    v = detect_stop_visits(
        p, mapping(), {"s": shape([(0, 0), (100, 0)])}, {"s": pattern}
    )
    assert v.arrive_ts.iloc[0] == pd.Timestamp("2026-11-01T07:00:00Z")
    p = pings(
        [0, 100], times=["2026-03-08T01:59:45-06:00", "2026-03-08T03:00:15-05:00"]
    )
    v = detect_stop_visits(
        p, mapping(), {"s": shape([(0, 0), (100, 0)])}, {"s": pattern}
    )
    assert v.arrive_ts.iloc[0] == pd.Timestamp("2026-03-08T08:00:00Z")


def test_trip_id_recurs_on_multiple_service_dates():
    from aggiepulse.transform.operations import _match_trips

    sched = schedule()
    previous = sched.copy()
    previous["service_date"] = "2026-10-31"
    previous["sched_arrival_ts"] -= pd.Timedelta(days=1)
    v = pd.DataFrame(
        {
            "_pass_id": ["p", "p"],
            "route_short_name": "01",
            "direction_id": 0,
            "stop_id": ["x", "y"],
            "stop_sequence": [0, 1],
            "arrive_ts": pd.to_datetime(
                ["2026-11-01T06:59:10Z", "2026-11-01T07:16:10Z"]
            ),
            "matched_trip_id": None,
            "sched_ts": pd.Series([pd.NaT] * 2, dtype="datetime64[ns, UTC]"),
            "delay_s": np.nan,
        }
    )
    matched = _match_trips(v, pd.concat([sched, previous]))
    assert matched.matched_trip_id.tolist() == ["a", "a"]


def test_missing_apc_stays_unknown():
    p = pings([0, 100])
    p["onboard"] = None
    pattern = pd.DataFrame(
        {"stop_id": ["x"], "stop_sequence": [0], "shape_dist_m": [50.0]}
    )
    v = detect_stop_visits(
        p, mapping(), {"s": shape([(0, 0), (100, 0)])}, {"s": pattern}
    )
    assert len(v) == 1
    assert v.net_board_proxy.isna().all()


def test_rejected_projection_breaks_interpolation():
    p = pings([0, 50, 100])
    p.loc[1, "lat"] = 500 / 111195.08
    pattern = pd.DataFrame(
        {"stop_id": ["x"], "stop_sequence": [0], "shape_dist_m": [75.0]}
    )
    assert detect_stop_visits(
        p, mapping(), {"s": shape([(0, 0), (100, 0)])}, {"s": pattern}
    ).empty
