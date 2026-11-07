"""Pure typed pandas parsers for decoded landing envelopes (no file I/O).

Events accept an iterable of JSONL envelopes; other parsers accept one envelope.
Invalid timestamps/numbers become NaT/NA. Raw categorical arrays are retained.
"""
from __future__ import annotations

from collections.abc import Iterable
from html import unescape
from typing import Any

import pandas as pd


def _typed(rows, columns, *, dates=(), numbers=(), integers=(), booleans=(), strings=()):
    frame = pd.DataFrame(rows, columns=columns)
    for name in dates:
        frame[name] = pd.to_datetime(frame[name], utc=True, errors='coerce', format='mixed')
    for name in numbers:
        frame[name] = pd.to_numeric(frame[name], errors='coerce').astype('Float64')
    for name in integers:
        frame[name] = pd.to_numeric(frame[name], errors='coerce').astype('Int64')
    for name in booleans:
        frame[name] = frame[name].map(_boolean).astype('boolean')
    for name in strings:
        frame[name] = frame[name].astype('string')
    return frame


def _boolean(value):
    if value is None or value == '':
        return False
    return str(value).lower() in {'1', 'true', 'yes'}


def parse_events(records: Iterable[dict[str, Any]]) -> pd.DataFrame:
    columns = ['id', 'title', 'start_ts', 'end_ts', 'is_all_day', 'location_title',
               'lat', 'lon', 'event_types', 'audience', 'campus', 'tags', 'group_title',
               'rsvp_total', 'is_canceled', 'ingest_ts']
    rows = []
    for record in records:
        event = record['payload']
        rows.append({
            'id': str(event['id']) if event.get('id') is not None else None,
            'title': unescape(event.get('title') or ''),
            'start_ts': event.get('date_utc'), 'end_ts': event.get('date2_utc'),
            'is_all_day': event.get('is_all_day'), 'location_title': event.get('location_title'),
            'lat': event.get('location_latitude'), 'lon': event.get('location_longitude'),
            'event_types': event.get('event_types') or [],
            'audience': event.get('event_types_audience') or [],
            'campus': event.get('event_types_campus') or [], 'tags': event.get('tags') or [],
            'group_title': event.get('group_title'), 'rsvp_total': event.get('rsvp_total'),
            'is_canceled': event.get('is_canceled'), 'ingest_ts': record.get('ingest_ts')})
    return _typed(rows, columns, dates=['start_ts', 'end_ts', 'ingest_ts'],
                  numbers=['lat', 'lon'], integers=['rsvp_total'],
                  booleans=['is_all_day', 'is_canceled'],
                  strings=['id', 'title', 'location_title', 'group_title'])


def parse_openmeteo(record: dict[str, Any]) -> pd.DataFrame:
    hourly = record['payload'].get('hourly') or {}
    fields = ['temperature_2m', 'apparent_temperature', 'precipitation', 'rain',
              'wind_speed_10m', 'weather_code']
    rows = []
    for i, ts in enumerate(hourly.get('time', [])):
        row = {'ts_hour': ts, 'kind': record.get('kind'), 'ingest_ts': record.get('ingest_ts')}
        for field in fields:
            values = hourly.get(field) or []
            row[field] = values[i] if i < len(values) else None
        rows.append(row)
    return _typed(rows, ['ts_hour', *fields, 'kind', 'ingest_ts'],
                  dates=['ts_hour', 'ingest_ts'], numbers=fields[:-1],
                  integers=['weather_code'], strings=['kind'])


def parse_nws(record: dict[str, Any]) -> pd.DataFrame:
    fields = {'temp_c': 'temperature', 'wind_kph': 'windSpeed',
              'precip_mm': 'precipitationLastHour', 'humidity_pct': 'relativeHumidity'}
    rows = []
    for feature in record['payload'].get('features') or []:
        props = feature.get('properties') or {}
        coords = (feature.get('geometry') or {}).get('coordinates') or []
        row = {'observation_id': feature.get('id'), 'ts': props.get('timestamp'),
               'lat': coords[1] if len(coords) > 1 else None,
               'lon': coords[0] if coords else None, 'ingest_ts': record.get('ingest_ts')}
        for output, source in fields.items():
            quantity = props.get(source) or {}
            value = quantity.get('value')
            # NWS precipitation quantities are normally metres, unlike Open-Meteo mm.
            if value is not None and output == 'precip_mm' and quantity.get('unitCode') == 'wmoUnit:m':
                value *= 1000
            row[output] = value
        rows.append(row)
    return _typed(rows, ['observation_id', 'ts', 'lat', 'lon', *fields, 'ingest_ts'],
                  dates=['ts', 'ingest_ts'], numbers=['lat', 'lon', *fields],
                  strings=['observation_id'])


def parse_ntd(record: dict[str, Any]) -> pd.DataFrame:
    fields = ['upt', 'vrm', 'vrh', 'voms']
    rows = [{'month': item.get('date'), 'ntd_id': item.get('ntd_id'),
             'agency': item.get('agency'), 'mode': item.get('mode'), 'tos': item.get('tos'),
             **{field: item.get(field) for field in fields}, 'ingest_ts': record.get('ingest_ts')}
            for item in record['payload']]
    return _typed(rows, ['month', 'ntd_id', 'agency', 'mode', 'tos', *fields, 'ingest_ts'],
                  dates=['month', 'ingest_ts'], numbers=['vrm', 'vrh'], integers=['upt', 'voms'],
                  strings=['ntd_id', 'agency', 'mode', 'tos'])
