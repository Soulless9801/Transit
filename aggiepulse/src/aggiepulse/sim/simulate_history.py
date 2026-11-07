"""Reproducible, explicitly simulated GTFS history. See ASSUMPTIONS.md."""
from __future__ import annotations

import argparse
import json
import zlib

import numpy as np
import pandas as pd

from aggiepulse.common.paths import LOCAL_TZ
from aggiepulse.features.event_exposure import exposure_from_gtfs, prepare_events
from aggiepulse.features.inputs import calendar_features, load_local, make_grid, utc

TURNOVER = 1.5
CLASS_WAVE_COEF = 1.0
# OUR ASSUMPTION (see ASSUMPTIONS.md): demand priors by route group, not fitted.
# (baseline weight, class-change weight); groups come from published GTFS names.
ROUTE_GROUP_WEIGHTS = {'on_campus': (1.0, 1.0), 'off_campus': (1.0, .30),
                       'night_weekend': (.80, .15), 'airport': (.50, .05),
                       'rellis': (.80, .15)}
ON_CAMPUS = {'01', '03', '04', '05', '06', '08'}
ROUTE_DIRECTION_SIGMA = .25
DAY_EFFECT_SIGMA = .20


def route_group(route_short_name, route_long_name=''):
    """Published GTFS numbering: 0x on campus, combined codes nights/weekends."""
    name, long_name = str(route_short_name), str(route_long_name or '').lower()
    if 'rellis' in long_name:
        return 'rellis'
    if 'airport' in long_name:
        return 'airport'
    if '-' in name or 'break' in long_name or 'night' in long_name:
        return 'night_weekend'
    return 'on_campus' if name in ON_CAMPUS else 'off_campus'


def route_direction_residual(route, direction, seed):
    """Mean-one lognormal idiosyncrasy keyed by name, so input row order cannot move it."""
    key = zlib.crc32(f'{route}|{int(direction)}'.encode())
    draw = np.random.default_rng([seed, key]).normal()
    return float(np.exp(ROUTE_DIRECTION_SIGMA*draw - ROUTE_DIRECTION_SIGMA**2/2))


def implied_boardings(history, turnover=TURNOVER):
    """Assumed gross boardings per scheduled trip; APC cannot measure turnover."""
    return history.avg_load_factor.fillna(0) * history.capacity * history.sched_trips * turnover


DAY_SCALE = {'class': 1., 'weekend': .40, 'holiday': .18, 'break': .22, 'reading': .55, 'finals': .65}


def month_day_weights(months, academic, periods):
    """Day-type demand weight for every local date of the given months (YYYY-MM)."""
    days = pd.DatetimeIndex([])
    for month in months:
        first = pd.Timestamp(month+'-01')
        days = days.append(pd.date_range(first, first+pd.offsets.MonthEnd(0), freq='D'))
    noon = (days+pd.Timedelta(hours=12)).tz_localize(LOCAL_TZ).tz_convert('UTC')
    cal = calendar_features(noon, academic, periods)
    return pd.Series(cal.day_type.map(DAY_SCALE).to_numpy(), index=days.strftime('%Y-%m-%d'))


def calibrate(history, ntd, turnover=TURNOVER, day_weights=None):
    """Scale bounded loads to latest available same-calendar-month MB/DO UPT.

    Partial months target the covered share of monthly UPT: flat calendar time
    by default, or day-type weighted when ``day_weights`` (local date -> weight
    for every day of each month) is given. This is a magnitude calibration, not
    an estimate of actual daily totals.
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
        if day_weights is None:
            fraction = len(covered) / (days * 96)
        else:
            month_weights = day_weights[day_weights.index.str.startswith(month)]
            covered_weight = covered.dt.strftime('%Y-%m-%d').map(day_weights).sum() / 96
            fraction = float(covered_weight / month_weights.sum())
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
                            'coverage_method': 'flat_calendar' if day_weights is None else 'day_type_weighted',
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
    day_scale = cal.day_type.map(DAY_SCALE).to_numpy()
    if observed is not None and not observed.empty:
        observed = observed[observed.window_start+pd.Timedelta(minutes=15) <= end]
    adjustment, observed_count = observed_profile_adjustment(grid, baseline*day_scale, observed)
    # Route heterogeneity is an explicit group prior plus a small keyed residual.
    long_names = dict(zip(gtfs['routes'].route_short_name.astype(str),
                          gtfs['routes'].route_long_name.astype(str)))
    route_keys = grid[['route_short_name','direction_id']].drop_duplicates()
    weights = {}
    for route, direction in route_keys.itertuples(index=False):
        base, wave = ROUTE_GROUP_WEIGHTS[route_group(route, long_names.get(str(route), ''))]
        residual = route_direction_residual(route, direction, seed)
        weights[(route, direction)] = (base*residual, wave*residual)
    keys = list(map(tuple, grid[['route_short_name','direction_id']].to_numpy()))
    base_weight = np.array([weights[key][0] for key in keys])
    wave_weight = np.array([weights[key][1] for key in keys])
    grid['sim_baseline'] = baseline*day_scale*adjustment*base_weight
    grid['sim_class_wave'] = cal.class_wave.to_numpy()*CLASS_WAVE_COEF*wave_weight
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
    # Window noise plus a shared route-direction x local-day demand level.
    day_key = grid.route_short_name.astype(str)+'|'+grid.direction_id.astype(str)+'|'+cal.date.to_numpy()
    codes, uniques = pd.factorize(day_key)
    day_effect = rng.lognormal(-DAY_EFFECT_SIGMA**2/2, DAY_EFFECT_SIGMA, len(uniques))[codes]
    grid['sim_noise'] = (rng.normal(0, .025, len(grid))
                         + demand*(rng.lognormal(-.35**2/2,.35,len(grid))*day_effect-1))
    raw = grid[['sim_baseline', 'sim_class_wave', 'sim_event', 'sim_weather', 'sim_noise']].sum(axis=1)
    served = grid.sched_trips > 0
    grid['avg_load_factor'] = np.where(served, np.maximum(raw, .005), 0.)
    grid['active_vehicles'] = np.maximum(grid.sched_trips - (rng.random(len(grid)) < .06), 0).astype(int)
    # Approximate departure-equivalent vehicles, not a fleet/block assignment.
    grid.loc[served & grid.active_vehicles.eq(0), 'active_vehicles'] = 1
    grid['service_ratio'] = grid.active_vehicles/grid.sched_trips.replace(0, np.nan)
    months = sorted(grid.window_start.dt.tz_convert(LOCAL_TZ).dt.strftime('%Y-%m').unique())
    grid = calibrate(grid, ntd, TURNOVER, month_day_weights(months, academic, periods))
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
    grid.attrs.update(metadata, seed=seed, turnover=TURNOVER, class_wave_coef=CLASS_WAVE_COEF,
                      day_effect_sigma=DAY_EFFECT_SIGMA, route_direction_sigma=ROUTE_DIRECTION_SIGMA,
                      route_group_weights={k: list(v) for k, v in ROUTE_GROUP_WEIGHTS.items()},
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
