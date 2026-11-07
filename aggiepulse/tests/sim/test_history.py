import pandas as pd
import pytest

from aggiepulse.features.inputs import calendar_features, schedule
from aggiepulse.sim.simulate_history import (
    calibrate,
    implied_boardings,
    simulate_history,
)


def test_deterministic_seed_and_contract(simulation_inputs):
    a = simulate_history(**simulation_inputs,seed=13)
    b = simulate_history(**simulation_inputs,seed=13)
    pd.testing.assert_frame_equal(a,b)
    c = simulate_history(**simulation_inputs,seed=14)
    assert not a.avg_load_factor.equals(c.avg_load_factor)
    assert set(a.data_origin)=={'simulated'}
    assert (a.n_pings==0).all()
    assert a.window_start.dt.minute.isin([0,15,30,45]).all()
    assert a.window_start.dt.tz is not None
    assert a.loc[a.sched_trips.eq(0),'p90_load_factor'].isna().all()
    assert (a.dropna().max_load_factor >= a.dropna().p90_load_factor).all()
    assert set(a.direction_id)=={0,1}


def test_ntd_calibration_with_partial_month_disclosed(simulation_inputs):
    frame = simulate_history(**simulation_inputs)
    report = frame.attrs['calibration'][0]
    assert report['ntd_month']=='2026-09'
    # Sep 6-14 covered: 3 weekend days (.4) + 6 weekdays; September: 8 weekend + 22 weekdays.
    fraction = (3*.4+6)/(8*.4+22)
    assert report['coverage_method']=='day_type_weighted'
    assert report['coverage_fraction']==pytest.approx(fraction)
    assert implied_boardings(frame).sum()==pytest.approx(10000*fraction, rel=1e-6)
    assert report['full_month_equivalent']==pytest.approx(10000,rel=1e-6)


def test_class_wave_before_start_after_end_and_no_weekend(simulation_inputs):
    times = pd.to_datetime(['2026-09-09T12:00Z','2026-09-09T12:45Z',
                             '2026-09-09T14:05Z','2026-09-12T12:45Z'],utc=True)
    f = calendar_features(times, simulation_inputs['academic'], simulation_inputs['periods'])
    assert f.class_wave[1] > f.class_wave[0]*10
    assert f.class_wave[2] > f.class_wave[0]*10
    assert f.class_wave[3]==0
    assert f.minutes_to_next_class_start[1]==15
    assert f.minutes_since_last_class_end[2]==5


def test_schedule_counts_trip_start_not_stops(simulation_inputs):
    schedule_frame = schedule(simulation_inputs['gtfs'],'2026-09-09T05:00Z','2026-09-10T05:00Z')
    assert schedule_frame.sched_trips.sum()==26


def test_ntd_latest_same_month_only(simulation_inputs):
    simulation_inputs['ntd'] = pd.concat([simulation_inputs['ntd'],pd.DataFrame({
        'month':pd.to_datetime(['2025-09-01','2027-09-01'],utc=True), 'upt':[500,999999],
        'ntd_id':['60278']*2,'mode':['MB']*2,'tos':['DO']*2})],ignore_index=True)
    frame = simulate_history(**simulation_inputs)
    assert frame.attrs['calibration'][0]['ntd_upt']==10000


def test_typed_silver_calendar_dates(simulation_inputs):
    gtfs = simulation_inputs['gtfs']
    expected = schedule(gtfs,'2026-09-09T05:00Z','2026-09-10T05:00Z')
    gtfs['calendar_dates']['date'] = pd.to_datetime(gtfs['calendar_dates'].date).dt.strftime('%Y-%m-%d')
    actual = schedule(gtfs,'2026-09-09T05:00Z','2026-09-10T05:00Z')
    pd.testing.assert_frame_equal(expected,actual)


def test_route_groups_from_published_names():
    from aggiepulse.sim.simulate_history import route_group
    assert route_group('01', 'Bonfire') == 'on_campus'
    assert route_group('36', 'Matthew Gaines') == 'off_campus'
    assert route_group('40-41', 'Break, Nights, & Weekends') == 'night_weekend'
    assert route_group('47', 'RELLIS') == 'rellis'
    assert route_group('07', 'Airport') == 'airport'


def test_route_residual_is_keyed_not_order_dependent():
    from aggiepulse.sim.simulate_history import route_direction_residual
    a = route_direction_residual('03', 1, 42)
    assert a == route_direction_residual('03', 1, 42)
    assert a != route_direction_residual('03', 0, 42)
    assert a != route_direction_residual('03', 1, 43)


def test_class_wave_not_diluted_by_block_count(simulation_inputs):
    periods = pd.concat([simulation_inputs['periods']]+[pd.DataFrame(
        {'pattern':['MWF'],'start_time':[f'{h:02d}:00'],'end_time':[f'{h:02d}:50']}) for h in range(12,18)])
    t = pd.to_datetime(['2026-09-09T12:45Z'],utc=True)  # 07:45 local, 15 min before 08:00
    few = calendar_features(t, simulation_inputs['academic'], simulation_inputs['periods']).class_wave[0]
    many = calendar_features(t, simulation_inputs['academic'], periods).class_wave[0]
    assert many == pytest.approx(few, rel=1e-3)
    assert few > .9


def test_flat_calibration_fallback(simulation_inputs):
    frame = simulate_history(**simulation_inputs)
    flat = calibrate(frame.assign(sim_baseline=0.,sim_class_wave=0.,sim_event=0.,sim_weather=0.,sim_noise=0.),
                     simulation_inputs['ntd'])
    assert flat.attrs['calibration'][0]['coverage_fraction']==pytest.approx(9/30)
