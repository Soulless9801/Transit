"""Reproducible, explicitly simulated GTFS history. See ASSUMPTIONS.md."""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from aggiepulse.common.paths import LOCAL_TZ
from aggiepulse.features.event_exposure import exposure_from_gtfs, prepare_events
from aggiepulse.features.inputs import calendar_features, load_local, make_grid, utc

TURNOVER = 2.0


def implied_boardings(history, turnover=TURNOVER):
    """Assumed gross boardings per scheduled trip; APC cannot measure turnover."""
    return history.avg_load_factor.fillna(0) * history.capacity * history.sched_trips * turnover


def calibrate(history, ntd, turnover=TURNOVER):
    """Scale bounded loads to latest available same-calendar-month MB/DO UPT.

    Partial months target a covered-calendar-day fraction of monthly UPT.
    This is a magnitude calibration, not an estimate of actual daily totals.
    """
    history = history.copy()
    reports = []
    local = history.window_start.dt.tz_convert(LOCAL_TZ)
    if ntd.empty:
        raise ValueError('NTD monthly UPT required for honest magnitude calibration')
    ntd = ntd.copy()
    ntd['month'] = pd.to_datetime(ntd.month, utc=True)
    for name, value in [('ntd_id', '60278'), ('mode', 'MB'), ('tos', 'DO')]:
        if name in ntd:
            ntd = ntd[ntd[name].astype(str).eq(value)]
    ntd = ntd.drop_duplicates(['month', 'mode', 'tos'], keep='last')
    for month, indices in history.groupby(local.dt.strftime('%Y-%m')).groups.items():
        available = ntd[(ntd.month.dt.month == int(month[-2:])) & (ntd.month <= utc(month+'-01'))]
        if available.empty:
            raise ValueError(f'No matching-calendar-month NTD available for {month}')
        row = available.sort_values('month').iloc[-1]
        dates = local.loc[indices].dt.strftime('%Y-%m-%d').nunique()
        days = pd.Timestamp(month+'-01').days_in_month
        # Last partial day contributes only its covered slots, including DST.
        covered = local.loc[indices].drop_duplicates()
        fraction = len(covered) / (days * 96)
        target = float(row.upt)*fraction
        raw = history.loc[indices, 'avg_load_factor'].to_numpy()
        weights = (history.loc[indices, 'capacity'] * history.loc[indices, 'sched_trips'] * turnover).to_numpy()
        maximum = float((1.25*(raw>0)*weights).sum())
        if target > maximum:
            raise ValueError(f'NTD calibration infeasible: target {target} > capacity bound {maximum}')
        low, high = 0., 1.
        while float((np.minimum(raw*high, 1.25)*weights).sum()) < target:
            high *= 2
        for _ in range(65):
            middle = (low+high)/2
            if float((np.minimum(raw*middle, 1.25)*weights).sum()) < target:
                low = middle
            else:
                high = middle
        scale = (low+high)/2
        history.loc[indices, 'avg_load_factor'] = np.minimum(raw*scale, 1.25)
        for col in ['baseline', 'class_wave', 'event', 'weather', 'noise']:
            history.loc[indices, 'sim_'+col] *= scale
        actual = float(implied_boardings(history.loc[indices], turnover).sum())
        reports.append({'sim_month': month, 'ntd_month': row.month.strftime('%Y-%m'),
                            'ntd_upt': int(row.upt), 'covered_days': int(dates), 'coverage_fraction': fraction,
                            'target_covered_upt': target, 'simulated_boardings': actual,
                            'full_month_equivalent': actual/fraction, 'scale': scale,
                            'relative_error': abs(actual-target)/target})
    history.attrs['calibration'] = reports
    return history


def observed_profile_adjustment(grid, baseline, observed):
    """Strong prior, weekend-only observations cannot fit weekday shape."""
    adjustment = np.ones(len(grid))
    if observed is None or observed.empty:
        return adjustment, 0
    obs = observed.dropna(subset=['avg_load_factor']).copy()
    local = obs.window_start.dt.tz_convert(LOCAL_TZ)
    obs['weekend'] = local.dt.dayofweek >= 5
    obs['hour'] = local.dt.hour
    glocal = grid.window_start.dt.tz_convert(LOCAL_TZ)
    for (weekend, hour), group in obs.groupby(['weekend', 'hour']):
        selected = ((glocal.dt.dayofweek >= 5) == weekend) & glocal.dt.hour.eq(hour)
        if not selected.any():
            continue
        # 500 pseudo-windows; shrink adjustment to [0.5, 1.5].
        weight = len(group)/(len(group)+500)
        prior = max(float(baseline[selected].mean()), .02)
        ratio = np.clip(float(group.avg_load_factor.mean())/prior, .5, 1.5)
        adjustment[selected] = 1+weight*(ratio-1)
    return adjustment, len(obs)


def simulate_history(gtfs, academic, periods, events, weather, ntd, observed=None,
                     start='2026-09-06', end=None, seed=42):
    """Return a pandas gold.route_dir_15m-compatible frame with debug columns.

    Date-only start is Chicago midnight. End is exclusive and defaults to the
    last completed UTC quarter-hour. Identical inputs/end/seed are deterministic.
    """
    if len(str(start)) == 10:
        start = pd.Timestamp(start, tz=LOCAL_TZ).tz_convert('UTC')
    end = utc(end if end is not None else pd.Timestamp.now(tz='UTC')).floor('15min')
    grid = make_grid(gtfs, start, end)
    if grid.empty:
        raise ValueError('Simulation interval is empty')
    rng = np.random.default_rng(seed)
    cal = calendar_features(grid.window_start, academic, periods)
    minute = cal.minute_of_day.to_numpy()
    baseline = (.035 + .23*np.exp(-.5*((minute-510)/115)**2)
                + .20*np.exp(-.5*((minute-735)/150)**2)
                + .25*np.exp(-.5*((minute-1005)/130)**2))
    day_scale = cal.day_type.map({'class':1., 'weekend':.40, 'holiday':.18,
                                 'break':.22, 'reading':.55, 'finals':.65}).to_numpy()
    if observed is not None and not observed.empty:
        observed = observed[observed.window_start+pd.Timedelta(minutes=15) <= end]
    adjustment, observed_count = observed_profile_adjustment(grid, baseline*day_scale, observed)
    # Route heterogeneity is a prior, not a fit to sparse weekend APC.
    route_keys = grid[['route_short_name','direction_id']].drop_duplicates()
    route_weights = dict(zip(map(tuple, route_keys.to_numpy()),
                            rng.lognormal(-.7**2/2, .7, len(route_keys)), strict=True))
    multiplier = np.array([route_weights[key] for key in
                           map(tuple, grid[['route_short_name','direction_id']].to_numpy())])
    grid['sim_baseline'] = baseline*day_scale*adjustment*multiplier
    grid['sim_class_wave'] = cal.class_wave.to_numpy()*.65*multiplier
    exposure = exposure_from_gtfs(grid, events, gtfs)
    grid['sim_event'] = exposure.event_exposure.to_numpy()*.12
    routes = gtfs['routes'].copy()
    capacities = dict(zip(routes.route_short_name,
                          np.where(routes.route_long_name.str.contains('RELLIS', case=False), 25, 85)))
    grid['capacity'] = grid.route_short_name.map(capacities).astype(int)
    lengths = pd.to_numeric(gtfs['stop_times'].shape_dist_traveled).groupby(
        gtfs['stop_times'].trip_id).max()
    trip_lengths = gtfs['trips'].merge(gtfs['routes'], on='route_id')
    trip_lengths['length'] = trip_lengths.trip_id.map(lengths)
    route_length = trip_lengths.groupby('route_short_name').length.median().to_dict()
    rain = np.zeros(len(grid))
    if not weather.empty:
        past = weather[~weather.is_forecast.fillna(False).astype(bool)].sort_values('ts_hour')
        past = past.drop_duplicates('ts_hour', keep='last')
        rain = grid.window_start.dt.floor('h').map(past.set_index('ts_hour').precip_mm).fillna(0).to_numpy(dtype=float)
    short = grid.route_short_name.map(route_length).fillna(10000).to_numpy() < 7000
    grid['sim_weather'] = np.minimum(rain/3., 1.) * np.where(short, .10, .03)
    demand = grid[['sim_baseline','sim_class_wave','sim_event','sim_weather']].sum(axis=1)
    grid['sim_noise'] = (rng.normal(0, .025, len(grid))
                         + demand*(rng.lognormal(-.35**2/2,.35,len(grid))-1))
    raw = grid[['sim_baseline', 'sim_class_wave', 'sim_event', 'sim_weather', 'sim_noise']].sum(axis=1)
    served = grid.sched_trips > 0
    grid['avg_load_factor'] = np.where(served, np.maximum(raw, .005), 0.)
    grid['active_vehicles'] = np.maximum(grid.sched_trips - (rng.random(len(grid)) < .06), 0).astype(int)
    # Approximate departure-equivalent vehicles, not a fleet/block assignment.
    grid.loc[served & grid.active_vehicles.eq(0), 'active_vehicles'] = 1
    grid['service_ratio'] = grid.active_vehicles/grid.sched_trips.replace(0, np.nan)
    grid = calibrate(grid, ntd)
    metadata = grid.attrs.copy()
    grid['p90_load_factor'] = np.where(served, np.minimum(grid.avg_load_factor*1.18+.035, 1.4), np.nan)
    grid['max_load_factor'] = np.where(served, np.minimum(grid.p90_load_factor+.07, 1.5), np.nan)
    grid['headway_cv'] = np.where(served, np.clip(.10+.50*grid.avg_load_factor
                                                +rng.normal(0,.04,len(grid)), 0, 1.5), np.nan)
    grid['bunching_rate'] = np.where(served, np.clip(.025+.24*grid.avg_load_factor
                                                  +rng.normal(0,.015,len(grid)), 0, 1), np.nan)
    grid['mean_delay_s'] = np.where(served, np.maximum(0,30+180*grid.avg_load_factor
                                                     +rng.normal(0,25,len(grid))), np.nan)
    grid['n_pings'] = 0  # Never imply that simulated APC pings were observed.
    grid['data_origin'] = 'simulated'
    component_cols = ['sim_'+c for c in ['baseline','class_wave','event','weather','noise']]
    grid.loc[~served, component_cols] = 0.
    grid['sim_clip_adjustment'] = grid.avg_load_factor-grid[component_cols].sum(axis=1)
    grid['avg_load_factor'] = grid.avg_load_factor.where(served)
    grid.attrs.update(metadata, seed=seed, turnover=TURNOVER,
                      simulation_start=utc(start).isoformat(), simulation_end=end.isoformat(),
                      observed_profile_windows=observed_count,
                      profile_note='500-window shrinkage prior; only observed day-types/hours adjusted')
    return grid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', default='2026-09-06')
    parser.add_argument('--end')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    inputs = load_local()
    events = prepare_events(inputs.pop('raw_events'), inputs.pop('venues'), inputs.pop('seed_root'))
    history = simulate_history(**inputs, events=events, start=args.start, end=args.end, seed=args.seed)
    history.to_parquet(args.output, index=False)
    print(json.dumps(history.attrs, indent=2))


if __name__ == '__main__':
    main()
