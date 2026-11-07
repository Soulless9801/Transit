"""Family-level signed contributions and event attribution (a simulated prior).

LightGBM ``pred_contrib`` gives exact per-row TreeSHAP values for the p50 model.
They are summed per feature family; the top three families by |contribution|
become ``top_factors``. Event uplift is prediction minus prediction with event
features neutralised: an attribution under simulated event assumptions, not a
causal effect.
"""
import numpy as np
import pandas as pd

FAMILIES = ('Time', 'Class change', 'Event', 'Weather', 'Recent load',
            'Bunching-service', 'Seasonal', 'Static')

EVENT_FEATURES = {'event_exposure', 'event_pre', 'event_post', 'event_during',
                  'football_home_game_day', 'event_timing_is_assumed',
                  'event_schedule_is_hindsight', 'unknown_size_events',
                  'minutes_to_next_big_event', 'minutes_since_last_big_event', 'venue_type'}
TIME_FEATURES = {'dow', 'minute_of_day', 'minute_of_day_sin', 'minute_of_day_cos', 'slot',
                 'weekend', 'week_of_term', 'days_to_finals', 'day_type', 'is_class_day',
                 'is_holiday', 'is_break', 'is_reading_day', 'is_finals'}
CLASS_FEATURES = {'minutes_to_next_class_start', 'minutes_since_last_class_end'}
WEATHER_PREFIXES = ('precip', 'weather', 'rain', 'heat', 'apparent', 'wind')
SEASONAL_FEATURES = {'seasonal_slot_mean', 'load_same_slot_last_week'}
RECENT_PREFIXES = ('load_', 'p90_load', 'delta_load')
SERVICE_PREFIXES = ('headway', 'bunching', 'service', 'active_', 'closed_', 'upcoming_alert')


def family(feature):
    if feature in EVENT_FEATURES:
        return 'Event'
    if feature in CLASS_FEATURES:
        return 'Class change'
    if feature in TIME_FEATURES:
        return 'Time'
    if feature.startswith(WEATHER_PREFIXES):
        return 'Weather'
    if feature in SEASONAL_FEATURES:
        return 'Seasonal'
    if feature.startswith(RECENT_PREFIXES):
        return 'Recent load'
    if feature.startswith(SERVICE_PREFIXES):
        return 'Bunching-service'
    return 'Static'


def neutralise_events(frame):
    """Counterfactual input with no nearby events (same rows, other features intact)."""
    result = frame.copy()
    for feature in EVENT_FEATURES & set(frame):
        if feature == 'venue_type':
            result[feature] = 'none'
        elif feature.startswith('minutes_'):
            result[feature] = np.nan
        else:
            result[feature] = 0.
    return result


def _clock(minutes):
    minutes = int(minutes) % 1440
    return f'{minutes//60:02d}:{minutes % 60:02d}'


def label(feature, value, row):
    """Cheap human-readable label; falls back to the raw feature name."""
    missing = value is None or (not isinstance(value, str) and bool(pd.isna(value)))
    if not missing and not isinstance(value, str):
        value = float(value)
    if feature == 'minutes_to_next_class_start' and not missing:
        return f'Next class starts in {value:.0f} min'
    if feature == 'minutes_since_last_class_end' and not missing:
        return f'Classes let out {value:.0f} min ago'
    if feature in CLASS_FEATURES and missing:
        return 'No more classes today'
    if feature == 'football_home_game_day' and value:
        return 'Home football game day'
    if feature == 'minutes_to_next_big_event' and not missing:
        return f'Big event nearby starts in {value:.0f} min'
    if feature == 'minutes_since_last_big_event' and not missing:
        return f'Big event nearby ended {value:.0f} min ago'
    if family(feature) == 'Event':
        venue = row.get('venue_type')
        return f'Nearby event ({venue})' if isinstance(venue, str) and venue != 'none' else 'Nearby events'
    if feature.startswith('precip') and not missing:
        when = 'next hour' if 'next' in feature else 'now'
        return f'Rain {when} {value:.1f} mm/h'
    if feature == 'apparent_c' and not missing:
        return f'Feels like {value:.0f} °C'
    if feature in {'load_t0', 'p90_load_t0'} and not missing:
        return f'Load now {value:.0%}' if feature == 'load_t0' else f'Peak load now {value:.0%}'
    if feature.startswith('load_lag'):
        return f'Load {feature[8:]} min ago ' + ('no data' if missing else f'{value:.0%}')
    if feature in {'load_t0', 'p90_load_t0', 'delta_load_30m'} and missing:
        return 'No recent bus data'
    if feature == 'delta_load_30m' and not missing:
        return f'Load change over 30 min {value:+.0%}'
    if feature in SEASONAL_FEATURES and missing:
        return 'No typical-load history for this slot'
    if feature == 'seasonal_slot_mean' and not missing:
        return f'Typical load for this slot {value:.0%}'
    if feature == 'load_same_slot_last_week' and not missing:
        return f'Same slot last week {value:.0%}'
    if feature in {'minute_of_day', 'minute_of_day_sin', 'minute_of_day_cos', 'slot'}:
        minute = row.get('minute_of_day')
        return f'Time of day {_clock(minute)}' if minute is not None and pd.notna(minute) else 'Time of day'
    if feature == 'day_type' and isinstance(value, str):
        return f'Day type: {value}'
    if feature == 'route_short_name':
        return f'Route {value}'
    if feature == 'direction_id' and not missing:
        return f'Direction {value:.0f}' if not isinstance(value, str) else f'Direction {value}'
    if feature == 'scheduled_frequency_per_hour' and not missing:
        return f'{value:.0f} scheduled trips/h'
    if feature.startswith(('headway_cv', 'bunching')) and not missing:
        return f'{"Headway irregularity" if feature.startswith("headway") else "Bunching"} {value:.2f}'
    if feature == 'service_shortfall' and not missing:
        return f'Service shortfall {value:.0%}'
    return feature.replace('_', ' ')


def top_factors(frame, contributions, columns, k=3):
    """Top-k families by |sum of SHAP values|, each named by its largest feature.

    ``contributions`` is LightGBM pred_contrib output (n_rows x (n_features+1));
    the final bias column is excluded. Contributions are in load-factor units.
    """
    families = np.array([family(c) for c in columns])
    names = sorted(set(families))
    results = []
    for row, values in zip(frame.to_dict('records'), np.asarray(contributions), strict=True):
        values = values[:len(columns)]
        totals = []
        for name in names:
            selected = np.flatnonzero(families == name)
            totals.append((name, float(values[selected].sum()), selected))
        totals.sort(key=lambda item: -abs(item[1]))
        factors = []
        for name, total, selected in totals[:k]:
            j = selected[np.argmax(np.abs(values[selected]))]
            feature = columns[j]
            value = row.get(feature)
            numeric = (float(value) if isinstance(value, (bool, int, float, np.number))
                       and pd.notna(value) else None)
            factors.append({'family': str(name), 'feature': label(feature, value, row),
                            'value': numeric, 'contribution': total})
        results.append(factors)
    return results
