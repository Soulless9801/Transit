import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def toy_training():
    """Small wide training frame with the columns the ML code relies on."""
    rng = np.random.default_rng(0)
    t0 = pd.date_range('2026-09-07T13:00Z', periods=400, freq='15min')
    rows = []
    for route in ['01', '36']:
        for direction in [0, 1]:
            exposure = np.where(np.arange(len(t0)) % 10 < 3, 20., 0.)
            state = .2+.1*np.sin(np.arange(len(t0))/8)+(.1 if route == '01' else 0)
            y = state+.01*exposure+rng.normal(0, .01, len(t0))
            rows.append(pd.DataFrame({
                'route_short_name': route, 'direction_id': direction, 't0': t0,
                'day_type': 'class', 'minute_of_day': (t0.hour*60+t0.minute).to_numpy(),
                'minutes_to_next_class_start': (np.arange(len(t0)) % 6)*15.,
                'load_t0': state, 'p90_load_t0': state*1.18+.035, 'load_lag30': state,
                'seasonal_slot_mean': state, 'precip_now': 0., 'event_exposure': exposure,
                'event_pre': exposure, 'football_home_game_day': 0., 'venue_type': np.where(exposure > 0, 'stadium', 'none'),
                'minutes_to_next_big_event': np.where(exposure > 0, 30., np.nan),
                'weather_is_observed_proxy': True,
                'y_p90load_h15': y, 'y_p90load_h30': y, 'y_p90load_h60': y,
                'y_crowd_h15': (y >= .5).astype(float), 'y_crowd_h30': (y >= .5).astype(float),
                'y_crowd_h60': (y >= .5).astype(float), 'data_origin': 'simulated'}))
    frame = pd.concat(rows, ignore_index=True)
    frame['split'] = np.select([frame.t0 < t0[240], frame.t0 < t0[320]], ['train', 'validation'], 'test')
    return frame
