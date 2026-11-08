"""Small deterministic functions; no Spark or network dependencies."""
from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('America/Chicago')


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def strip_html(value):
    parser = _Text()
    parser.feed(value or '')
    return ' '.join(' '.join(parser.parts).split())


def gps_time_utc(value, ingest_ts):
    """Vendor collector policy: latest valid fold no later than ingestion."""
    if not value or not ingest_ts:
        return None
    try:
        local = datetime.fromisoformat(value)
        ingest = datetime.fromisoformat(ingest_ts) if isinstance(ingest_ts, str) else ingest_ts
        if ingest.tzinfo is None:  # Spark TIMESTAMP UDF arguments use session UTC.
            ingest = ingest.replace(tzinfo=UTC)
        if local.tzinfo:
            return local.astimezone(UTC)
        candidates = {
            local.replace(tzinfo=ZONE, fold=fold).astimezone(UTC)
            for fold in (0, 1)
            if local.replace(tzinfo=ZONE, fold=fold).astimezone(UTC)
            .astimezone(ZONE).replace(tzinfo=None) == local
        }
        if len(candidates) == 1:
            return candidates.pop()
        return max((c for c in candidates if c <= ingest), default=None)
    except (ValueError, TypeError):
        return None


def gtfs_seconds(value):
    if not value:
        return None
    try:
        h, m, s = map(int, value.split(':'))
        return h * 3600 + m * 60 + s if h >= 0 and 0 <= m < 60 and 0 <= s < 60 else None
    except (ValueError, TypeError):
        return None


def gtfs_instant(service_date, seconds):
    noon = datetime.fromisoformat(str(service_date) + 'T12:00:00').replace(tzinfo=ZONE)
    return noon.astimezone(UTC) + timedelta(seconds=seconds - 43200)


def norm(value):
    return unescape(str(value or '')).strip()


def number(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def truth(value):
    return str(value).lower() in ('1', 'true', 'yes')


def normalize_event(payload, venues, config):
    """Apply the versioned seed policy, retaining the first rejection reason."""
    event = json.loads(payload) if isinstance(payload, str) else payload
    config = json.loads(config) if isinstance(config, str) else config
    venues = json.loads(venues) if isinstance(venues, str) else venues
    event = {k: [norm(x) for x in v] if isinstance(v, list) else norm(v)
             for k, v in event.items()}
    location = event.get('location_title', '')
    venue = next((v for v in sorted(venues, key=lambda v: -len(v.get('display_name') or ''))
                  if any(norm(a).casefold() in location.casefold() for a in
                         (v.get('aliases') or v.get('display_name') or '').split('|') if a)), {})
    lat = number(event.get('location_latitude'))
    lon = number(event.get('location_longitude'))
    lat = lat if lat is not None else number(venue.get('lat'))
    lon = lon if lon is not None else number(venue.get('lon'))
    reason = 'kept'
    for rule in config['drop_rules']:
        value = event.get(rule['field'], '')
        values = value if isinstance(value, list) else [value]
        if ('regex' in rule and any(re.search(rule['regex'], norm(v), re.IGNORECASE) for v in values)
                or 'equals' in rule and any(truth(v) == truth(rule['equals']) for v in values)):
            reason = rule['reason']
            break
    if reason == 'kept' and re.search(r'\bstudy sessions?\b', event.get('title', ''), re.IGNORECASE):
        reason = 'routine service or study availability'
    south, west, north, east = config['local_bbox']
    local = lat is not None and lon is not None and south <= lat <= north and west <= lon <= east
    if reason == 'kept' and not (venue or local):
        reason = 'missing local physical evidence'
    types = event.get('event_types') or []
    audience = event.get('event_types_audience') or []
    campuses = event.get('event_types_campus') or []
    capacity = number(venue.get('capacity'))
    rsvp = number(event.get('rsvp_total'))
    size = capacity if capacity is not None else rsvp
    category_weight = max((config['category_weights'][x] for x in types if x in config['category_weights']),
                          default=config['scoring']['unknown_category_weight'])
    audience_weight = max((config['audience_weights'][x] for x in audience if x in config['audience_weights']),
                          default=config['scoring']['unknown_audience_weight'])
    return json.dumps({
        'event_id': event.get('id'), 'title': strip_html(event.get('title')), 'start_ts': event.get('date_utc'),
        'end_ts': event.get('date2_utc') or None, 'is_all_day': truth(event.get('is_all_day')),
        'location_title': location, 'venue_key': venue.get('venue_key'), 'lat': lat, 'lon': lon,
        'campus': '|'.join(campuses) or venue.get('campus'), 'category': types[0] if types else None,
        'event_types': types, 'audience': audience, 'group_title': event.get('group_title'),
        'rsvp_total': int(rsvp) if rsvp is not None else None,
        'is_athletics': 'Sports & Athletics' in types,
        'is_football_home': bool(venue.get('venue_key') == 'kyle_field' and
                              re.search(r'football', event.get('title', ''), re.IGNORECASE)),
        'est_attendance': int(size) if size is not None else None,
        'significance': math.log1p(max(size, 0)) * category_weight * audience_weight
        if size is not None else None, 'is_canceled': truth(event.get('is_canceled')), 'reason': reason,
    })


def closed_stop_codes(description):
    """Only closed-stop lists, excluding temporary or disruption-boundary stops."""
    lists = re.findall(r'following bus stops? will be closed.*?<ul>(.*?)</ul>',
                       description or '', flags=re.IGNORECASE | re.DOTALL)
    return sorted(set(re.findall(r'\bID:\s*([^)<\s]+)', ' '.join(lists))))


# APC movement is derived from GPS, not undocumented vendor speed units.
APC_MAX_GAP_SECONDS = 120
APC_STUCK_SECONDS = 3600


def apc_stuck(elapsed_seconds, lat_span, lon_span, first_stop, last_stop):
    """Conservative warning: >=60 min flat, >100m GPS span, >=2 close stops.

    Callers break flat runs at count/capacity/route changes or gaps >120s.
    Stops count only when the ping is within 50m of its nearest GTFS stop.
    Min/max stop IDs establish at least two distinct stops, not a trip match.
    """
    import math

    span_m = 111000 * math.hypot(float(lat_span or 0), float(lon_span or 0) * .86)
    return bool(elapsed_seconds is not None and elapsed_seconds >= APC_STUCK_SECONDS
                and span_m > 100 and first_stop is not None and last_stop is not None
                and first_stop != last_stop)
