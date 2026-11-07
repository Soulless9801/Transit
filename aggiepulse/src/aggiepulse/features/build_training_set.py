"""Leakage-safe pandas features, explicit target-window labels and time splits."""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from aggiepulse.common.paths import LOCAL_TZ
from aggiepulse.features.event_exposure import haversine_m
from aggiepulse.features.inputs import (
    KEY,
    calendar_features,
    measured_windows,
    route_stops,
    utc,
)

HORIZONS = (15, 30, 60)


def static_features(gtfs, venues):
    points = route_stops(gtfs)
    st = gtfs['stop_times'].copy()
    st['length'] = pd.to_numeric(st.shape_dist_traveled)
    lengths = st.groupby('trip_id').length.max()
    trips = gtfs['trips'].merge(gtfs['routes'][['route_id','route_short_name']], on='route_id')
    trips['route_length_m'] = trips.trip_id.map(lengths)
    trips['direction_id'] = trips.direction_id.astype(int)
    result = points.groupby(KEY[:2]).stop_id.nunique().rename('stop_count').reset_index()
    result = result.merge(trips.groupby(KEY[:2]).route_length_m.median().reset_index(), on=KEY[:2])
    for venue_name in ['kyle_field', 'msc']:
        venue = venues[venues.venue_key.eq(venue_name)].iloc[0]
        points['near'] = haversine_m(points.stop_lat.to_numpy(), points.stop_lon.to_numpy(),
                                     venue.lat, venue.lon) <= 600
        near = points.groupby(KEY[:2]).near.max().rename('serves_'+venue_name).reset_index()
        result = result.merge(near, on=KEY[:2])
    return result


def weather_features(times, weather, allow_observed_proxy=True):
    """Select forecasts issued <= t0; observed next-hour substitute is flagged.

    Archive-only substitutions are the explicitly requested hindsight exception.
    Missing weather remains missing (never silently encoded as dry).
    """
    result = pd.DataFrame({'t0': pd.DatetimeIndex(times).unique().sort_values()})
    columns = ['precip_now', 'precip_next_hour', 'apparent_c', 'wind_kph']
    for col in columns:
        result[col] = np.nan
    result['weather_is_observed_proxy'] = False
    result['weather_now_is_observed_proxy'] = False
    result['weather_forecast_issued_ts'] = pd.NaT
    result['weather_forecast_issued_ts'] = pd.to_datetime(result.weather_forecast_issued_ts, utc=True)
    if weather.empty:
        result['rain_onset'] = np.nan
        result['heat_gt_35c'] = np.nan
        return result
    w = weather.copy()
    w['ts_hour'] = pd.to_datetime(w.ts_hour, utc=True)
    w['forecast_issued_ts'] = pd.to_datetime(w.forecast_issued_ts, utc=True)
    w['is_forecast'] = w.is_forecast.fillna(False).astype(bool)
    groups = {hour: group for hour, group in w.groupby('ts_hour')}
    for i, t0 in enumerate(result.t0):
        for future in [False, True]:
            target = t0.floor('h') + pd.Timedelta(hours=int(future))
            group = groups.get(target)
            if group is None:
                continue
            forecasts = group[group.is_forecast & group.forecast_issued_ts.le(t0)]
            proxy = False
            if not forecasts.empty:
                selected = forecasts.sort_values('forecast_issued_ts').iloc[-1]
            else:
                archive = group[~group.is_forecast]
                if archive.empty or not allow_observed_proxy:
                    continue
                selected = archive.sort_values('forecast_issued_ts').iloc[-1]
                proxy = True
            result.loc[i, 'precip_next_hour' if future else 'precip_now'] = selected.precip_mm
            if future:
                result.loc[i, 'weather_is_observed_proxy'] = proxy
                if not proxy:
                    result.loc[i, 'weather_forecast_issued_ts'] = selected.forecast_issued_ts
            else:
                result.loc[i, 'weather_now_is_observed_proxy'] = proxy
                for col in ['apparent_c', 'wind_kph']:
                    result.loc[i, col] = selected.get(col, np.nan)
    result['rain_onset'] = np.where(result.precip_now.notna() & result.precip_next_hour.notna(),
                                    (result.precip_now.eq(0) & result.precip_next_hour.gt(0)).astype(float), np.nan)
    result['heat_gt_35c'] = np.where(result.apparent_c.notna(), result.apparent_c.gt(35).astype(float), np.nan)
    return result


def build_training_set(history, academic, periods, event_exposure, weather,
                       static=None, train_end=None, validation_end=None,
                       allow_observed_weather_proxy=True):
    """Build wide horizon rows. Null targets are retained, never made negatives.

    Default split boundaries are 70/85% of the simulated time range, fixed once
    per call; production callers should persist/pass explicit boundaries. Labels
    crossing a split boundary are purged. Observed targets are holdout-only.
    Separate per-horizon origins prevent accidental simulated/observed mixing.
    """
    source = measured_windows(history)
    source['window_start'] = pd.to_datetime(source.window_start, utc=True)
    source['route_short_name'] = source.route_short_name.astype('string')
    source['direction_id'] = source.direction_id.astype(int)
    source = source.sort_values(KEY+['data_origin'])
    if source.duplicated(KEY+['data_origin']).any():
        raise ValueError('Duplicate route/direction/window/origin input')
    # At a shared key, measured APC replaces the synthetic prior.
    source = source.drop_duplicates(KEY, keep='first').reset_index(drop=True)
    origins = set(source.data_origin.unique())
    if not origins <= {'simulated','observed'}:
        raise ValueError(f'Invalid origins: {origins}')
    sim_times = source.loc[source.data_origin.eq('simulated'), 'window_start']
    if sim_times.empty:
        sim_times = source.window_start
    start, end = sim_times.min(), sim_times.max()+pd.Timedelta(minutes=15)
    train_end = utc(train_end) if train_end is not None else (start+(end-start)*.70).floor('15min')
    validation_end = utc(validation_end) if validation_end is not None else (start+(end-start)*.85).floor('15min')
    if not start < train_end < validation_end:
        raise ValueError('Require start < train_end < validation_end')
    # Origins are isolated in recent state; observed windows never get synthetic lag state.
    join_key = KEY[:2]+['state_origin']
    frame = source[KEY+['data_origin']].rename(columns={'window_start':'t0','data_origin':'state_origin'})
    frame = frame.reset_index(drop=True)
    cal = calendar_features(frame.t0, academic, periods)
    frame = pd.concat([frame, cal.drop(columns=['date','notes','class_wave'])], axis=1)
    angle = frame.minute_of_day*2*np.pi/1440
    frame['minute_of_day_sin'], frame['minute_of_day_cos'] = np.sin(angle), np.cos(angle)
    frame['slot'] = frame.minute_of_day//15
    lookup = source.rename(columns={'data_origin':'state_origin'})
    recent_columns = ['avg_load_factor','service_ratio','headway_cv','bunching_rate','active_vehicles']
    for lag, name in [(15,'t0'), (30,'lag15'), (45,'lag30'), (75,'lag60')]:
        shifted = lookup[join_key+['window_start']+recent_columns].copy()
        shifted['t0'] = shifted.window_start+pd.Timedelta(minutes=lag)
        names = {'avg_load_factor':'load_'+name}
        if lag == 15:
            names.update({c:c for c in recent_columns[1:]})
        else:
            names.update({c:c+'_'+name for c in recent_columns[1:]})
        shifted = shifted.drop(columns='window_start').rename(columns=names)
        frame = frame.merge(shifted, on=join_key+['t0'], how='left', validate='one_to_one')
    frame['delta_load_30m'] = frame.load_t0-frame.load_lag30
    frame['service_shortfall'] = (1-frame.service_ratio).clip(lower=0)
    frame['headway_cv_last30m'] = frame[['headway_cv','headway_cv_lag15']].mean(axis=1)
    frame['bunching_last30m'] = frame[['bunching_rate','bunching_rate_lag15']].mean(axis=1)
    # Exactly seven local calendar days, preserving slot across DST.
    prior_local = frame.t0.dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)-pd.Timedelta(days=7)
    prior = prior_local.dt.tz_localize(LOCAL_TZ, ambiguous=False, nonexistent='NaT').dt.tz_convert('UTC')
    last_week = frame[join_key].copy()
    last_week['window_start'] = prior
    last_week = last_week.merge(lookup[join_key+['window_start','avg_load_factor']],
                                on=join_key+['window_start'], how='left')
    frame['load_same_slot_last_week'] = last_week.avg_load_factor.to_numpy()
    # Expanding train-only statistics, available only AFTER each source interval ends.
    season = source[source.data_origin.eq('simulated') & (source.window_start < train_end)
                    & source.avg_load_factor.notna()].copy().reset_index(drop=True)
    season_cal = calendar_features(season.window_start, academic, periods)
    season['day_type'] = season_cal.day_type.to_numpy()
    season['slot'] = (season_cal.minute_of_day//15).to_numpy()
    season_key = KEY[:2]+['day_type','slot']
    season = season.sort_values('window_start')
    group = season.groupby(season_key)
    season['seasonal_slot_mean'] = group.avg_load_factor.cumsum()/(group.cumcount()+1)
    season['available_at'] = season.window_start+pd.Timedelta(minutes=15)
    if season.empty:
        frame['seasonal_slot_mean'] = np.nan
    else:
        frame = pd.merge_asof(frame.sort_values('t0'),
                              season[season_key+['available_at','seasonal_slot_mean']].sort_values('available_at'),
                              left_on='t0', right_on='available_at', by=season_key, direction='backward')
        frame = frame.drop(columns='available_at')
    frame = frame.merge(weather_features(frame.t0, weather, allow_observed_weather_proxy), on='t0', how='left')
    if not event_exposure.empty:
        ex = event_exposure.drop_duplicates(KEY).rename(columns={'window_start':'t0'})
        frame = frame.merge(ex, on=KEY[:2]+['t0'], how='left', validate='many_to_one')
    if static is not None:
        frame = frame.merge(static, on=KEY[:2], how='left', validate='many_to_one')
    # Current/future schedule is published information, unlike target load.
    freq = lookup[join_key+['window_start','sched_trips']].rename(
        columns={'window_start':'t0','sched_trips':'scheduled_trips_t0'})
    frame = frame.merge(freq, on=join_key+['t0'], how='left')
    frame['scheduled_frequency_per_hour'] = frame.scheduled_trips_t0*4
    for hook in ['active_alert', 'closed_stop_count', 'upcoming_alert_60m']:
        frame[hook] = np.nan
    frame['split'] = np.select([frame.state_origin.eq('observed'), frame.t0 < train_end,
                                 frame.t0 < validation_end], ['observed_holdout','train','validation'], 'test')
    for horizon in HORIZONS:
        labels = lookup[join_key+['window_start','p90_load_factor','max_load_factor','headway_cv']].copy()
        labels['t0'] = labels.window_start-pd.Timedelta(minutes=horizon)
        labels[f'y_p90load_h{horizon}'] = labels.p90_load_factor
        labels[f'y_crowd_h{horizon}'] = labels.max_load_factor.ge(.75).astype('Float64').where(
            labels.max_load_factor.notna())
        labels[f'y_hwcv_h{horizon}'] = labels.headway_cv
        labels[f'data_origin_h{horizon}'] = labels.state_origin
        label_cols = [f'y_p90load_h{horizon}', f'y_crowd_h{horizon}', f'y_hwcv_h{horizon}',
                      f'data_origin_h{horizon}']
        frame = frame.merge(labels[join_key+['t0']+label_cols], on=join_key+['t0'], how='left')
        label_end = frame.t0+pd.Timedelta(minutes=horizon+15)
        crossing = ((frame.split.eq('train') & (label_end > train_end))
                    | (frame.split.eq('validation') & (label_end > validation_end)))
        frame.loc[crossing, label_cols] = pd.NA
    # Retain partial-horizon rows, including sparse real APC labels; one origin per row.
    label_origins = frame[[f'data_origin_h{h}' for h in HORIZONS]]
    frame['data_origin'] = label_origins.bfill(axis=1).iloc[:, 0]
    frame = frame[frame.data_origin.notna()].copy()
    frame = frame.drop(columns='state_origin').sort_values(KEY[:2]+['t0','data_origin']).reset_index(drop=True)
    frame.attrs.update(train_end=train_end.isoformat(), validation_end=validation_end.isoformat(),
                       state_semantics='load_t0 = [t0-15m,t0), completed aggregate',
                       seasonal_semantics='expanding simulated-training-only mean; frozen after train_end',
                       observed_weather_proxy_allowed=allow_observed_weather_proxy)
    return frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--train-end')
    parser.add_argument('--validation-end')
    args = parser.parse_args()
    from aggiepulse.features.event_exposure import exposure_from_gtfs, prepare_events
    from aggiepulse.features.inputs import load_local
    inputs = load_local()
    history = pd.read_parquet(args.history)
    events = prepare_events(inputs['raw_events'], inputs['venues'], inputs['seed_root'])
    exposure = exposure_from_gtfs(history[KEY].drop_duplicates(), events, inputs['gtfs'])
    training = build_training_set(history, inputs['academic'], inputs['periods'], exposure,
                                  inputs['weather'], static_features(inputs['gtfs'], inputs['venues']),
                                  args.train_end, args.validation_end)
    training.to_parquet(args.output, index=False)
    print(json.dumps({'rows':len(training), 'columns':len(training.columns), **training.attrs}, indent=2))


if __name__ == '__main__':
    main()
