import numpy as np
import pandas as pd
import pytest

from aggiepulse.ml.explanations import (
    FAMILIES,
    family,
    label,
    neutralise_events,
    top_factors,
)
from aggiepulse.ml.train import fit_horizon


@pytest.mark.parametrize('feature,expected', [
    ('minutes_to_next_class_start', 'Class change'), ('minutes_since_last_class_end', 'Class change'),
    ('minute_of_day_sin', 'Time'), ('day_type', 'Time'), ('is_class_day', 'Time'), ('week_of_term', 'Time'),
    ('event_exposure', 'Event'), ('football_home_game_day', 'Event'), ('venue_type', 'Event'),
    ('precip_next_hour', 'Weather'), ('apparent_c', 'Weather'), ('heat_gt_35c', 'Weather'),
    ('load_t0', 'Recent load'), ('p90_load_t0', 'Recent load'), ('delta_load_30m', 'Recent load'),
    ('seasonal_slot_mean', 'Seasonal'), ('load_same_slot_last_week', 'Seasonal'),
    ('headway_cv_last30m', 'Bunching-service'), ('service_shortfall', 'Bunching-service'),
    ('active_vehicles', 'Bunching-service'), ('route_short_name', 'Static'), ('route_length_m', 'Static')])
def test_family_mapping(feature, expected):
    assert family(feature) == expected
    assert expected in FAMILIES


def test_top_factors_sum_families_signed():
    columns = ['minute_of_day_sin', 'slot', 'load_t0', 'event_exposure', 'precip_now']
    frame = pd.DataFrame({'minute_of_day_sin': [.1], 'slot': [40], 'minute_of_day': [600],
                          'load_t0': [.42], 'event_exposure': [12.], 'precip_now': [0.]})
    contributions = np.array([[.05, .04, -.07, .02, .001, .3]])  # last column is the bias
    factors = top_factors(frame, contributions, columns)[0]
    assert [f['family'] for f in factors] == ['Time', 'Recent load', 'Event']
    assert factors[0]['contribution'] == pytest.approx(.09)
    assert factors[1]['contribution'] == pytest.approx(-.07)
    assert factors[1]['feature'] == 'Load now 42%' and factors[1]['value'] == pytest.approx(.42)
    assert set(factors[0]) == {'family', 'feature', 'value', 'contribution'}


def test_labels_handle_missing_and_strings():
    assert label('load_lag30', np.nan, {}) == 'Load 30 min ago no data'
    assert label('minutes_to_next_class_start', 12., {}) == 'Next class starts in 12 min'
    assert label('route_short_name', '03', {}) == 'Route 03'


def test_neutralise_events_only_touches_event_features(toy_training):
    neutral = neutralise_events(toy_training)
    assert (neutral.event_exposure == 0).all() and (neutral.venue_type == 'none').all()
    assert neutral.minutes_to_next_big_event.isna().all()
    pd.testing.assert_series_equal(neutral.load_t0, toy_training.load_t0)


def test_event_uplift_is_prediction_minus_neutralised(toy_training):
    model, _, _ = fit_horizon(toy_training, 30, trials=1, log=False)
    test = toy_training[toy_training.split.eq('test')].reset_index(drop=True)
    output = model.forecast(test)
    neutral = model.forecast(neutralise_events(test), explain=False)
    no_event = test.event_exposure.eq(0).to_numpy()
    assert np.allclose(output.event_uplift[no_event], 0)
    assert np.allclose(output.event_uplift, output.p50-neutral.p50, atol=1e-9)
    assert output.event_uplift[~no_event].mean() > 0
    assert (output.p10 <= output.p50).all() and (output.p50 <= output.p90).all()
    assert output.p_crowd.between(0, 1).all()
    assert all(len(f) == 3 for f in output.top_factors)
    assert 'weather_is_observed_proxy' not in model.columns
