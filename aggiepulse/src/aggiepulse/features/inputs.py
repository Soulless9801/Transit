"""Shared, offline input normalization and GTFS schedule helpers."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

from aggiepulse.collector.parse import flatten_vehicles
from aggiepulse.common.paths import LOCAL_TZ, REPO_ROOT, data_root
from aggiepulse.ingest import parsers
from aggiepulse.ingest.gtfs import latest_gtfs_zip, load_gtfs

KEY = ['route_short_name', 'direction_id', 'window_start']
FLAGS = ['is_class_day', 'is_holiday', 'is_break', 'is_reading_day', 'is_finals']


def utc(value):
    value = pd.Timestamp(value)
    return value.tz_localize('UTC') if value.tzinfo is None else value.tz_convert('UTC')


def envelopes(source, root=None):
    for path in sorted((Path(root or data_root()) / 'landing' / source).rglob('*')):
        if not path.is_file() or not (path.name.endswith('.json') or path.name.endswith('.gz')):
            continue
        opener = gzip.open if path.suffix == '.gz' else open
        with opener(path, 'rt') as stream:
            for line in stream:
                if line.strip():
                    yield json.loads(line)


def concat(frames):
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def route_stops(gtfs):
    trips = gtfs['trips'].merge(gtfs['routes'][['route_id', 'route_short_name']], on='route_id')
    points = trips[['trip_id', 'route_short_name', 'direction_id']].merge(
        gtfs['stop_times'][['trip_id', 'stop_id']], on='trip_id').drop_duplicates()
    points = points.merge(gtfs['stops'], on='stop_id')
    points['direction_id'] = points.direction_id.astype(int)
    for column in ['stop_lat', 'stop_lon']:
        points[column] = pd.to_numeric(points[column])
    return points.drop(columns='trip_id').drop_duplicates()


def schedule(gtfs, start, end):
    """Trip starts, not stop-time counts. GTFS service-day noon handles DST."""
    st = gtfs['stop_times'].copy()
    st['seq'] = pd.to_numeric(st.stop_sequence)
    first = st.sort_values('seq').drop_duplicates('trip_id')
    trips = gtfs['trips'].merge(first[['trip_id', 'departure_time']], on='trip_id')
    trips = trips.merge(gtfs['routes'][['route_id', 'route_short_name']], on='route_id')
    dates = gtfs['calendar_dates'].query("exception_type == '1'").copy()
    dates['day'] = pd.to_datetime(dates.date.astype('string').str.replace('-', '', regex=False), format='%Y%m%d')
    dates = dates[(dates.day >= utc(start).tz_convert(LOCAL_TZ).tz_localize(None).normalize()
                   - pd.Timedelta(days=1)) & (dates.day <= utc(end).tz_convert(LOCAL_TZ)
                                              .tz_localize(None).normalize())]
    rows = trips.merge(dates[['service_id', 'day']], on='service_id')
    seconds = rows.departure_time.str.split(':').map(
        lambda t: int(t[0]) * 3600 + int(t[1]) * 60 + int(t[2]))
    noon = (rows.day + pd.Timedelta(hours=12)).dt.tz_localize(LOCAL_TZ).dt.tz_convert('UTC')
    rows['window_start'] = (noon + pd.to_timedelta(seconds - 43200, unit='s')).dt.floor('15min')
    rows['direction_id'] = rows.direction_id.astype(int)
    rows = rows[(rows.window_start >= utc(start)) & (rows.window_start < utc(end))]
    return rows.groupby(KEY).size().rename('sched_trips').reset_index()


def make_grid(gtfs, start, end):
    rd = gtfs['trips'].merge(gtfs['routes'][['route_id', 'route_short_name']], on='route_id')
    rd = rd[['route_short_name', 'direction_id', 'trip_headsign']].drop_duplicates(
        ['route_short_name', 'direction_id']).rename(columns={'trip_headsign': 'direction_name'})
    rd['direction_id'] = rd.direction_id.astype(int)
    times = pd.DataFrame({'window_start': pd.date_range(utc(start).ceil('15min'),
                                                       utc(end).floor('15min'),
                                                       freq='15min', inclusive='left')})
    grid = rd.merge(times, how='cross').merge(schedule(gtfs, start, end), on=KEY, how='left')
    grid['sched_trips'] = grid.sched_trips.fillna(0).astype(int)
    return grid.sort_values(KEY).reset_index(drop=True)


def calendar_features(times, academic, periods):
    local = pd.DatetimeIndex(times).tz_convert(LOCAL_TZ)
    result = pd.DataFrame({'date': local.strftime('%Y-%m-%d'), 'dow': local.day_of_week,
                           'minute_of_day': local.hour * 60 + local.minute})
    academic = academic.copy()
    academic['date'] = pd.to_datetime(academic.date).dt.strftime('%Y-%m-%d')
    result = result.merge(academic[['date', *FLAGS, 'week_of_term', 'days_to_finals', 'notes']],
                          on='date', how='left', validate='many_to_one')
    result[FLAGS] = result[FLAGS].fillna(0).astype(int)
    result['weekend'] = (result.dow >= 5).astype(int)
    result['day_type'] = np.select([result.is_holiday.eq(1), result.is_break.eq(1),
                                    result.is_reading_day.eq(1), result.is_finals.eq(1),
                                    result.weekend.eq(1)],
                                   ['holiday', 'break', 'reading', 'finals', 'weekend'], 'class')
    result['minutes_to_next_class_start'] = np.nan
    result['minutes_since_last_class_end'] = np.nan
    result['class_wave'] = 0.0
    for indices in result.groupby('date').groups.values():
        sub = result.loc[indices]
        if not sub.is_class_day.iloc[0]:
            continue
        dow = int(sub.dow.iloc[0])
        # Registrar's redefined Tuesday follows the Friday timetable.
        if sub.notes.fillna('').str.contains('attend Friday', case=False).any():
            dow = 4
        character = ['M', 'T', 'W', 'R', 'F', 'S', 'U'][dow]
        blocks = periods[periods.pattern.str.contains(character, regex=False)]
        mins = sub.minute_of_day.to_numpy()[:, None]
        def clock(column, blocks=blocks):
            return blocks[column].str.split(':').map(lambda t: int(t[0])*60 + int(t[1])).to_numpy()
        starts, ends = clock('start_time'), clock('end_time')
        if not len(starts):
            continue
        to_start, since_end = starts - mins, mins - ends
        result.loc[indices, 'minutes_to_next_class_start'] = np.where(
            to_start >= 0, to_start, np.inf).min(axis=1)
        result.loc[indices, 'minutes_since_last_class_end'] = np.where(
            since_end >= 0, since_end, np.inf).min(axis=1)
        wave = (np.exp(-0.5*((to_start-15)/10)**2).mean(axis=1)
                + np.exp(-0.5*((since_end-5)/10)**2).mean(axis=1)) * 2.0
        result.loc[indices, 'class_wave'] = wave
    return result.replace([np.inf, -np.inf], np.nan)


def measured_windows(frame):
    """An observed-origin schedule scaffold is not a measured target window."""
    if frame.empty:
        return frame.copy()
    truth = ['avg_load_factor','p90_load_factor','max_load_factor','headway_cv']
    available = [name for name in truth if name in frame]
    measured = frame[available].notna().any(axis=1)
    return frame[~frame.data_origin.eq('observed') | measured].copy()


def observed_from_raw(gtfs, root=None):
    bases = list(envelopes('tripspark_base', root))
    base = bases[-1]['payload'] if bases else {}
    rows = []
    for record in envelopes('tripspark_vehicles', root):
        if record.get('http_status') == 200:
            rows.extend(flatten_vehicles(record['payload'], record['ingest_ts'], base))
    if not rows:
        return pd.DataFrame()
    pings = pd.DataFrame(rows).dropna(subset=['load_factor', 'ts_gps_utc', 'route_short_name'])
    pings = pings.drop_duplicates(['vehicle_id', 'ts_gps_utc'])
    pings['ts_gps_utc'] = pd.to_datetime(pings.ts_gps_utc, utc=True)
    pings = pings[(pings.load_factor >= 0) & (pings.load_factor <= 1.5)]
    # Direction names must match GTFS headsigns; ambiguous/unmatched ones are excluded.
    names = gtfs['trips'].merge(gtfs['routes'], on='route_id')[
        ['route_short_name', 'direction_id', 'trip_headsign']].drop_duplicates()
    names['direction_id'] = names.direction_id.astype(int)
    names['name'] = names.trip_headsign.str.casefold().str.strip()
    names = names.drop_duplicates(['route_short_name', 'name'], keep=False)
    pings['name'] = pings.direction_name.str.casefold().str.strip()
    pings = pings.merge(names[['route_short_name', 'direction_id', 'name']],
                        on=['route_short_name', 'name'])
    pings['window_start'] = pings.ts_gps_utc.dt.floor('15min')
    agg = pings.groupby(KEY).agg(avg_load_factor=('load_factor', 'mean'),
                                 p90_load_factor=('load_factor', lambda x: x.quantile(.9)),
                                 max_load_factor=('load_factor', 'max'),
                                 active_vehicles=('vehicle_id', 'nunique'),
                                 n_pings=('vehicle_id', 'size')).reset_index()
    if agg.empty:
        return agg
    agg = agg.merge(schedule(gtfs, agg.window_start.min(),
                              agg.window_start.max()+pd.Timedelta(minutes=15)), on=KEY, how='left')
    agg['sched_trips'] = agg.sched_trips.fillna(0).astype(int)
    agg['service_ratio'] = agg.active_vehicles / agg.sched_trips.replace(0, np.nan)
    # APC alone cannot establish stop-arrival headways or delay.
    for column in ['headway_cv', 'bunching_rate', 'mean_delay_s']:
        agg[column] = np.nan
    directions = names.drop_duplicates(['route_short_name','direction_id'])
    agg = agg.merge(directions[['route_short_name','direction_id','trip_headsign']],
                    on=['route_short_name','direction_id'], how='left').rename(
                        columns={'trip_headsign':'direction_name'})
    agg['data_origin'] = 'observed'
    return agg


def seed_file(seed_root, name):
    """Seeds can be direct CSVs or immutable content-hash files in stem folders."""
    seed_root = Path(seed_root)
    direct = seed_root/name
    if direct.exists():
        return direct
    candidates = sorted((seed_root/Path(name).stem).glob("*"+Path(name).suffix))
    if not candidates:
        raise FileNotFoundError(f"Missing seed {name} under {seed_root}")
    return candidates[-1]


def load_local(root=None, seed_root=None, preloaded=None):
    root = Path(root or data_root())
    seed_root = Path(seed_root or REPO_ROOT / 'seeds')
    preloaded = preloaded or {}
    gtfs = preloaded.get('gtfs')
    if gtfs is None:
        zips = sorted((root/'landing'/'gtfs').rglob('*.zip'))
        gtfs = load_gtfs(zips[-1] if zips else latest_gtfs_zip())
    weather = concat(parsers.parse_openmeteo(r) for r in envelopes('weather_openmeteo', root))
    if not weather.empty:
        weather = weather.rename(columns={'precipitation':'precip_mm',
                                          'apparent_temperature':'apparent_c',
                                          'wind_speed_10m':'wind_kph'})
        weather['is_forecast'] = weather.kind.eq('forecast')
        weather['forecast_issued_ts'] = weather.ingest_ts
        weather = weather.drop_duplicates(['ts_hour', 'kind', 'ingest_ts'])
    ntd = concat(parsers.parse_ntd(r) for r in envelopes('ntd', root))
    # Retain raw forecast vintages even when silver keeps only the latest issue.
    if 'weather' in preloaded:
        weather = concat([weather, preloaded['weather']]).drop_duplicates(
            ['ts_hour', 'is_forecast', 'forecast_issued_ts'], keep='last')
    def seed(name):
        return preloaded[name] if name in preloaded else pd.read_csv(seed_file(seed_root, name+'.csv'))
    result = {'gtfs': gtfs, 'weather': weather, 'ntd': preloaded.get('ntd', ntd),
                'raw_events': list(envelopes('events', root)) if 'events' not in preloaded else [],
                'academic': preloaded['academic'] if 'academic' in preloaded else pd.read_csv(seed_file(seed_root, 'academic_days.csv')),
                'periods': preloaded['periods'] if 'periods' in preloaded else pd.read_csv(seed_file(seed_root, 'class_periods.csv')),
                'venues': seed('venues'), 'seed_root': seed_root,
                'observed': preloaded['observed'] if 'observed' in preloaded else observed_from_raw(gtfs, root)}
    if 'events' in preloaded:
        result['events'] = preloaded['events']
    return result
