"""Filtered, distance-decayed event priors at route/direction/15-minute grain."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from aggiepulse.common.paths import LOCAL_TZ
from aggiepulse.features.inputs import KEY, route_stops, seed_file
from aggiepulse.ingest.parsers import parse_events

try:
    import h3
except ImportError:
    h3 = None


def haversine_m(lat1, lon1, lat2, lon2):
    a, b = np.radians(lat1), np.radians(lat2)
    dlat, dlon = b-a, np.radians(lon2)-np.radians(lon1)
    return 6371000 * 2 * np.arcsin(np.sqrt(np.clip(
        np.sin(dlat/2)**2 + np.cos(a)*np.cos(b)*np.sin(dlon/2)**2, 0, 1)))


def prepare_events(raw_events, venues, seed_root):
    """Apply seed filter policy and supplement missing home athletics instances.

    Missing attendance remains null; it is never interpreted as zero attendance.
    Historical schedule snapshots are explicitly marked as hindsight.
    """
    import yaml

    seed_root = Path(seed_root)
    config = yaml.safe_load(seed_file(seed_root, 'event_filters.yaml').read_text())
    records = list(raw_events)
    parsed = parse_events(records)
    rows = []
    for event, envelope in zip(parsed.to_dict('records'), records, strict=True):
        payload = envelope['payload']
        policy_event = {**payload, 'campus': event['campus']}
        rejected = False
        for rule in config['drop_rules']:
            value = policy_event.get(rule['field'])
            values = value if isinstance(value, list) else [value]
            if 'regex' in rule:
                rejected |= any(re.search(rule['regex'], str(v or ''), re.IGNORECASE) is not None
                                for v in values)
            elif 'equals' in rule:
                rejected |= str(value).lower() in {str(rule['equals']).lower(), 'true'}
        if rejected or pd.isna(event['start_ts']):
            continue
        title, location = event['title'], str(event['location_title'] or '')
        venue = None
        for candidate in venues.to_dict('records'):
            aliases = str(candidate['aliases']).split('|')
            if any(re.search(r'(?<!\w)' + re.escape(alias) + r'(?!\w)', location, re.IGNORECASE)
                   for alias in aliases):
                venue = candidate
                break
        lat = venue['lat'] if venue else event['lat']
        lon = venue['lon'] if venue else event['lon']
        if pd.isna(lat) or pd.isna(lon):
            continue
        south, west, north, east = config['local_bbox']
        if not (south <= float(lat) <= north and west <= float(lon) <= east):
            continue
        capacity = venue.get('capacity') if venue else None
        size = capacity if pd.notna(capacity) else event.get('rsvp_total')
        cw = max([config['category_weights'].get(c, 1.) for c in event['event_types']] or [1.])
        aw = max([config['audience_weights'].get(a, 1.) for a in event['audience']] or [1.])
        significance = np.log1p(float(size))*cw*aw if pd.notna(size) and float(size)>0 else np.nan
        football = bool(re.search('football', title, re.IGNORECASE) and venue
                        and venue['venue_key'] == 'kyle_field')
        end = event['end_ts']
        duration = 240 if football else (1440 if event['is_all_day'] else 120)
        if pd.isna(end) or end <= event['start_ts']:
            end = event['start_ts'] + pd.Timedelta(minutes=duration)
        rows.append({'event_id': event['id'], 'title': title, 'start_ts': event['start_ts'],
                         'end_ts': end, 'lat': float(lat), 'lon': float(lon), 'significance': significance,
                         'venue_norm': venue['venue_key'] if venue else location,
                         'venue_type': venue['venue_type'] if venue else 'unknown',
                         'is_football_home': football, 'is_all_day': event['is_all_day'],
                         'published_ts': payload.get('published_ts'), 'timing_is_assumed': False})
    for file, football, duration in [('football_home.csv', True, 240),
                                      ('basketball_home.csv', False, 120)]:
        for item in pd.read_csv(seed_file(seed_root, file)).to_dict('records'):
            if item['home_away_neutral'] != 'home':
                continue
            venue = venues[venues.venue_key.eq(item['venue_key'])].iloc[0]
            day = str(item['date'])
            sport = 'football' if football else 'basketball'
            canceled = parsed[parsed.is_canceled.fillna(False)
                              & parsed.start_ts.dt.tz_convert(LOCAL_TZ).dt.strftime('%Y-%m-%d').eq(day)]
            if any(sport in str(c['title']).lower()
                   and any(alias.lower() in str(c['location_title']).lower()
                           for alias in str(venue.aliases).split('|'))
                   for c in canceled.to_dict('records')):
                continue  # A safety-net seed must never resurrect a canceled event.
            # Dedup athletics by venue, sport and local date, not calendar ID.
            if any(r['venue_norm'] == item['venue_key'] and r['is_football_home'] == football
                   and r['start_ts'].tz_convert(LOCAL_TZ).strftime('%Y-%m-%d') == day
                   and (football or re.search('basketball', r['title'], re.IGNORECASE)) for r in rows):
                continue
            assumed = pd.isna(item['kickoff_local'])
            clock = '14:00' if assumed else item['kickoff_local']
            start = pd.Timestamp(f'{day} {clock}', tz=LOCAL_TZ).tz_convert('UTC')
            rows.append({'event_id': f'seed-{file}-{day}-{item.get("team", "football")}',
                             'title': f'Home {"football" if football else "basketball"}: {item["opponent"]}',
                             'start_ts': start, 'end_ts': start+pd.Timedelta(minutes=duration),
                             'lat': float(venue.lat), 'lon': float(venue.lon),
                             'significance': np.log1p(float(venue.capacity))*2.5*1.5,
                             'venue_norm': venue.venue_key, 'venue_type': venue.venue_type,
                             'is_football_home': football, 'is_all_day': False, 'published_ts': None,
                             'timing_is_assumed': assumed})
    result = pd.DataFrame(rows)
    if result.empty:
        return pd.DataFrame(columns=['start_ts','end_ts','lat','lon','significance'])
    result['published_ts'] = pd.to_datetime(result.published_ts, utc=True)
    return result.drop_duplicates(['event_id', 'start_ts'], keep='last').reset_index(drop=True)


def build_event_exposure(grid, events, stops, radius_m=600.):
    """H3 candidate prefilter plus exact haversine; finite event kernels.

    ``stops`` is route_stops(gtfs). Unknown event publication times are flagged.
    No target/history values enter this function.
    """
    result = grid[KEY].copy().reset_index(drop=True)
    for column in ['event_exposure', 'event_pre', 'event_post', 'event_during',
                   'football_home_game_day', 'event_timing_is_assumed',
                   'event_schedule_is_hindsight', 'unknown_size_events']:
        result[column] = 0.0
    result['minutes_to_next_big_event'] = np.nan
    result['minutes_since_last_big_event'] = np.nan
    result['venue_type'] = 'none'
    result['event_geo_method'] = 'h3+haversine' if h3 else 'haversine'
    if events.empty:
        return result
    events = events.copy()
    events['start_ts'] = pd.to_datetime(events.start_ts, utc=True)
    events['end_ts'] = pd.to_datetime(events.end_ts, utc=True)
    if 'published_ts' not in events:
        events['published_ts'] = pd.NaT
    event_rows = events.to_dict('records')
    for (route, direction), indices in result.groupby(KEY[:2]).groups.items():
        points = stops[(stops.route_short_name == route) & (stops.direction_id == direction)]
        if points.empty:
            continue
        cells = set()
        if h3:
            for point in points.itertuples():
                cells.update(h3.grid_disk(h3.latlng_to_cell(point.stop_lat, point.stop_lon, 9), 3))
        times = pd.DatetimeIndex(result.loc[indices, 'window_start'])
        nanoseconds = times.as_unit('ns').asi8
        local_dates = times.tz_convert(LOCAL_TZ).strftime('%Y-%m-%d').to_numpy()
        accum = {column: np.zeros(len(times)) for column in [
            'event_pre','event_post','event_during','football_home_game_day',
            'event_timing_is_assumed','event_schedule_is_hindsight','unknown_size_events']}
        next_event, last_event = np.full(len(times), np.inf), np.full(len(times), np.inf)
        venue_types = np.full(len(times), 'none', dtype=object)
        distances = {}
        for event in event_rows:
            lat, lon = event['lat'], event['lon']
            if pd.isna(lat) or pd.isna(lon):
                continue
            coordinate = (lat, lon)
            if coordinate not in distances:
                candidate = not h3 or h3.latlng_to_cell(lat, lon, 9) in cells
                distances[coordinate] = (float(haversine_m(
                    points.stop_lat.to_numpy(), points.stop_lon.to_numpy(), lat, lon).min())
                    if candidate else np.inf)
            distance = distances[coordinate]
            if distance > radius_m:
                continue
            pre = (event['start_ts'].value-nanoseconds)/60e9
            post = (nanoseconds-event['end_ts'].value)/60e9
            published = event.get('published_ts')
            known = np.ones(len(times), dtype=bool) if pd.isna(published) else nanoseconds >= pd.Timestamp(published).value
            before = np.where((pre >= 0) & (pre <= 180), np.exp(-.5*((pre-45)/60)**2), 0.)
            after = np.where((post >= 0) & (post <= 180), np.exp(-.5*((post-20)/40)**2), 0.)
            during = ((pre < 0) & (post < 0)).astype(float)*.15
            affected = ((before+after+during)*known)>0
            weight = event.get('significance', np.nan)
            if pd.isna(weight):
                accum['unknown_size_events'] += affected
                continue
            exposure = weight*np.exp(-distance/300.)*known
            for col, kernel in [('event_pre',before),('event_post',after),('event_during',during)]:
                accum[col] += exposure*kernel
            venue_types[affected] = event.get('venue_type', 'unknown')
            accum['event_schedule_is_hindsight'][affected] = np.maximum(
                accum['event_schedule_is_hindsight'][affected], float(pd.isna(published)))
            accum['event_timing_is_assumed'][affected] = np.maximum(
                accum['event_timing_is_assumed'][affected], float(event.get('timing_is_assumed', False)))
            if weight >= 10:
                if pd.isna(published):
                    accum['event_schedule_is_hindsight'][:] = 1.
                next_event = np.minimum(next_event, np.where((pre>=0)&known,pre,np.inf))
                last_event = np.minimum(last_event, np.where((post>=0)&known,post,np.inf))
            if bool(event.get('is_football_home', False)):
                accum['football_home_game_day'] = np.maximum(accum['football_home_game_day'],
                    (local_dates == event['start_ts'].tz_convert(LOCAL_TZ).strftime('%Y-%m-%d'))*known)
        for column, values in accum.items():
            result.loc[indices, column] = values
        result.loc[indices, 'event_exposure'] = accum['event_pre']+accum['event_post']+accum['event_during']
        result.loc[indices, 'minutes_to_next_big_event'] = np.where(np.isfinite(next_event),next_event,np.nan)
        result.loc[indices, 'minutes_since_last_big_event'] = np.where(np.isfinite(last_event),last_event,np.nan)
        result.loc[indices, 'venue_type'] = venue_types
    return result


def exposure_from_gtfs(grid, events, gtfs):
    return build_event_exposure(grid, events, route_stops(gtfs))
