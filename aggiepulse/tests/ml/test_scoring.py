import datetime

import numpy as np
import pandas as pd
import pytest

from aggiepulse.features.build_training_set import (
    build_inference_features,
    build_training_set,
)
from aggiepulse.features.inputs import schedule
from aggiepulse.ml.score import (
    PREDICTION_COLUMNS,
    choose_t0,
    score_frame,
    to_spark_rows,
)

CONTRACT = ['scoring_ts', 't0', 'route_short_name', 'direction_id', 'direction_name', 'horizon_min',
            'pred_p10', 'pred_p50', 'pred_p90', 'p_crowd', 'event_uplift', 'top_factors', 'model_name',
            'model_version', 'feature_staleness_min', 'training_data_origin']


@pytest.fixture
def gtfs():
    days = pd.date_range('2026-11-01', '2026-11-10')
    trips, times = [], []
    for direction in [0, 1]:
        for hour in range(7, 20):
            for minute in (0, 30):
                trip = f't{direction}-{hour}-{minute}'
                trips.append({'trip_id': trip, 'route_id': 'r', 'service_id': 's',
                              'direction_id': str(direction), 'trip_headsign': f'Head {direction}'})
                times.append({'trip_id': trip, 'stop_id': '0', 'stop_sequence': '0',
                              'departure_time': f'{hour:02d}:{minute:02d}:00', 'shape_dist_traveled': '0'})
    return {'routes': pd.DataFrame([{'route_id': 'r', 'route_short_name': '01', 'route_long_name': 'Bonfire'}]),
            'trips': pd.DataFrame(trips), 'stop_times': pd.DataFrame(times),
            'stops': pd.DataFrame({'stop_id': ['0'], 'stop_lat': ['30.61'], 'stop_lon': ['-96.34']}),
            'calendar_dates': pd.DataFrame({'service_id': 's', 'date': days.strftime('%Y%m%d'),
                                            'exception_type': '1'})}


@pytest.fixture
def calendars():
    days = pd.date_range('2026-11-01', '2026-11-10')
    academic = pd.DataFrame({'date': days.strftime('%Y-%m-%d'), 'is_class_day': (days.day_of_week < 5).astype(int),
                             'is_holiday': 0, 'is_break': 0, 'is_reading_day': 0, 'is_finals': 0,
                             'week_of_term': 10, 'days_to_finals': 30, 'notes': ''})
    periods = pd.DataFrame({'pattern': ['MWF', 'TR'], 'start_time': ['08:00'] * 2, 'end_time': ['08:50'] * 2})
    return academic, periods


def observed_history(gtfs):
    sched = schedule(gtfs, '2026-11-09T13:00Z', '2026-11-09T20:00Z')
    sched = sched[sched.sched_trips > 0].copy()
    load = .2+.01*np.arange(len(sched))
    return sched.assign(avg_load_factor=load, p90_load_factor=load+.05, max_load_factor=load+.1,
                        active_vehicles=1, service_ratio=1., headway_cv=np.nan, bunching_rate=np.nan,
                        data_origin='observed')


def test_inference_features_match_training_builder(gtfs, calendars):
    academic, periods = calendars
    history = observed_history(gtfs)
    training = build_training_set(history, academic, periods, pd.DataFrame(), pd.DataFrame(),
                                  train_end='2026-11-09T22:00Z', validation_end='2026-11-09T23:00Z')
    t0 = pd.Timestamp('2026-11-09T17:00Z')
    inference = build_inference_features(history, gtfs, pd.DataFrame(), academic, periods,
                                         lambda grid: pd.DataFrame(), pd.DataFrame(), None, t0,
                                         scoring_ts=t0+pd.Timedelta(minutes=7))
    assert len(inference) == 2 and set(inference.direction_id) == {0, 1}
    row = inference[inference.direction_id.eq(0)].iloc[0]
    expected = training[training.t0.eq(t0) & training.direction_id.eq(0)].iloc[0]
    for column in ['load_t0', 'p90_load_t0', 'load_lag15', 'load_lag30', 'load_lag60', 'delta_load_30m',
                   'minutes_to_next_class_start', 'minute_of_day', 'day_type', 'scheduled_trips_t0',
                   'service_shortfall']:
        assert row[column] == expected[column] or (pd.isna(row[column]) and pd.isna(expected[column])), column
    # Departures leave at :00/:30, so the newest observed window is [16:30,16:45): 22 min old at 17:07.
    assert row.feature_staleness_min == pytest.approx(22)
    # Future observed windows never leak into inference features.
    future = history.assign(avg_load_factor=np.where(history.window_start >= t0, 9., history.avg_load_factor))
    again = build_inference_features(future, gtfs, pd.DataFrame(), academic, periods,
                                     lambda grid: pd.DataFrame(), pd.DataFrame(), None, t0)
    pd.testing.assert_series_equal(inference.load_t0, again.load_t0)


def test_choose_t0_uses_latest_complete_window():
    observed = pd.DataFrame({'window_start': pd.to_datetime(['2026-11-07T20:00Z', '2026-11-07T20:15Z'], utc=True),
                             'avg_load_factor': [.1, .2], 'data_origin': 'observed'})
    assert choose_t0(observed, pd.Timestamp('2026-11-07T20:38Z')) == pd.Timestamp('2026-11-07T20:30Z')
    assert choose_t0(observed, pd.Timestamp('2026-11-07T20:52Z')) == pd.Timestamp('2026-11-07T20:30Z')
    assert choose_t0(observed, pd.Timestamp('2026-11-07T23:52Z')) == pd.Timestamp('2026-11-07T23:45Z')
    assert choose_t0(pd.DataFrame(), pd.Timestamp('2026-11-07T20:52Z')) == pd.Timestamp('2026-11-07T20:45Z')


class FakeModel:
    def __init__(self):
        self.metadata = {'training_data_origin': 'simulated'}

    def forecast(self, frame):
        n = len(frame)
        return pd.DataFrame({'p10': [.1]*n, 'p50': [.2]*n, 'p90': [.4]*n, 'p_crowd': [.05]*n,
                             'event_uplift': [.01]*n,
                             'top_factors': [[{'family': 'Time', 'feature': 'Time of day 11:00', 'value': None,
                                               'contribution': .03}]]*n})


def test_scoring_output_matches_contract(gtfs):
    t0 = pd.Timestamp('2026-11-09T17:00Z')
    features = pd.DataFrame({'route_short_name': ['01', '01'], 'direction_id': [0, 1],
                             'feature_staleness_min': [7., np.nan]})
    versions = {h: {'name': f'transit.ml.crowding_h{h}', 'version': '3'} for h in (15, 30, 60)}
    output = score_frame(features, {h: FakeModel() for h in (15, 30, 60)}, versions, gtfs, t0+pd.Timedelta(minutes=7), t0)
    assert list(output.columns) == CONTRACT == PREDICTION_COLUMNS
    # Target windows 17:15 and 18:00 UTC have departures; 17:30 does too (every 30 min).
    assert set(output.horizon_min) <= {15, 30, 60} and len(output) > 0
    assert set(output.direction_name) == {'Head 0', 'Head 1'}
    rows = to_spark_rows(output)
    assert all(len(r) == len(CONTRACT) for r in rows)
    first = dict(zip(CONTRACT, rows[0], strict=True))
    assert isinstance(first['scoring_ts'], datetime.datetime) and first['scoring_ts'].tzinfo is None
    assert isinstance(first['direction_id'], int) and isinstance(first['horizon_min'], int)
    assert first['top_factors'] == [('Time', 'Time of day 11:00', None, .03)]
    assert any(dict(zip(CONTRACT, r, strict=True))['feature_staleness_min'] is None for r in rows)


def test_spark_schema_matches_contract():
    pytest.importorskip('pyspark')
    from aggiepulse.ml.score import prediction_schema
    schema = prediction_schema()
    assert schema.fieldNames() == CONTRACT
    factor = schema['top_factors'].dataType.elementType
    assert factor.fieldNames() == ['family', 'feature', 'value', 'contribution']


def test_inference_accepts_spark_round_tripped_profile(gtfs, calendars):
    academic, periods = calendars
    history = observed_history(gtfs)
    t0 = pd.Timestamp('2026-11-09T17:00Z')
    season = pd.DataFrame({'route_short_name': pd.Series(['01', '01'], dtype='str'),
                           'direction_id': np.array([0, 1], dtype='int32'), 'day_type': ['class', 'class'],
                           'slot': np.array([44, 44], dtype='int64'),
                           'available_at': pd.to_datetime(['2026-10-01', '2026-10-01']).tz_localize('UTC').as_unit('us'),
                           'seasonal_slot_mean': [.3, .4]})
    frame = build_inference_features(history, gtfs, season, academic, periods, lambda grid: pd.DataFrame(),
                                     pd.DataFrame(), None, t0)
    assert frame.sort_values('direction_id').seasonal_slot_mean.tolist() == [.3, .4]
