"""AggiePulse core Lakeflow pipeline. Every published name is fully qualified.

Bronze streams are append-only. Silver materialized views deliberately deduplicate
across all landing history: a bounded streaming watermark could reintroduce old
laptop replays. GPS/location/GTFS/weather work stays in Spark; only timezone and
small event/HTML policy functions use Python.
"""
import hashlib
import importlib
import inspect
import sys

from pyspark import pipelines as dp
from pyspark.sql import Window
from pyspark.sql import functions as F

sys.path.insert(0, spark.conf.get('aggiepulse.core_path'))  # noqa: F821
import lib.helpers as _helpers

importlib.reload(_helpers)
from lib.helpers import (
    closed_stop_codes,
    gps_time_utc,
    normalize_event,
    strip_html,
)

CAT = spark.conf.get('aggiepulse.catalog', 'transit')  # noqa: F821
ROOT = spark.conf.get('aggiepulse.root', '/Volumes/transit/raw')  # noqa: F821
# Make helper edits visible to MV planning, even when the raw inputs do not change.
CORE_REVISION = hashlib.sha256(inspect.getsource(_helpers).encode()).hexdigest()
spark.conf.set('spark.sql.session.timeZone', 'UTC')  # noqa: F821


def name(layer, table):
    return f'{CAT}.{layer}.{table}'


def read(layer, table):
    return spark.read.table(name(layer, table))  # noqa: F821


def sql(query):
    return spark.sql(query)  # noqa: F821


def json_value(path):
    return F.get_json_object('value', '$.' + path)


def register_json(table, source=None):
    @dp.table(name=name('bronze', table), table_properties={'delta.appendOnly': 'true'})
    @dp.expect('valid_json_envelope', '_rescued_data IS NULL')
    def bronze_json():
        raw = (spark.readStream.format('cloudFiles')  # noqa: F821
               .option('cloudFiles.format', 'text').option('recursiveFileLookup', 'true')
               .option('pathGlobFilter', '*.json*').load(f'{ROOT}/landing/{source or table}'))
        frame = raw.select(
            F.try_to_timestamp(json_value('ingest_ts')).alias('ingest_ts'),
            json_value('poll_id').alias('poll_id'),
            F.expr("try_cast(get_json_object(value, '$.http_status') AS INT)").alias('http_status'),
            json_value('payload').alias('payload'), json_value('kind').alias('kind'),
            json_value('query_start').alias('query_start'), json_value('query_end').alias('query_end'),
            json_value('query_url').alias('query_url'),
            F.col('_metadata.file_path').alias('_source_file'),
            F.when(F.try_to_timestamp(json_value('ingest_ts')).isNull() | json_value('payload').isNull(),
                   F.col('value')).alias('_rescued_data'),
        )
        if table == 'ntd_monthly':
            monthly = 'ARRAY<STRUCT<date:STRING,ntd_id:STRING,agency:STRING,mode:STRING,tos:STRING,upt:STRING,vrm:STRING,vrh:STRING,voms:STRING>>'
            return (frame.withColumn('_n', F.explode(F.from_json('payload', monthly)))
                    .select('*', '_n.*').drop('_n'))
        return frame


for _table, _source in [('tripspark_vehicles', None), ('tripspark_base', None),
                         ('events', None), ('weather_openmeteo', None),
                         ('weather_nws', None), ('ntd_monthly', 'ntd')]:
    register_json(_table, _source)


def register_csv(table, path):
    @dp.table(name=name('bronze', table), table_properties={'delta.appendOnly': 'true'})
    def bronze_csv():
        return (spark.readStream.format('cloudFiles')  # noqa: F821
                .option('cloudFiles.format', 'csv').option('header', 'true')
                .option('cloudFiles.inferColumnTypes', 'false')
                .option('cloudFiles.schemaEvolutionMode', 'rescue')
                .option('rescuedDataColumn', '_rescued_data')
                .option('recursiveFileLookup', 'true')
                .load(path).select('*', F.col('_metadata.file_path').alias('_source_file'),
                                   F.current_timestamp().alias('ingest_ts')))


GTFS = ('routes', 'stops', 'trips', 'stop_times', 'shapes', 'calendar_dates', 'feed_info')
SEEDS = ('academic_days', 'venues', 'class_periods', 'football_home', 'basketball_home')
for _table in GTFS:
    register_csv('gtfs_' + _table, f'{ROOT}/landing/gtfs_extracted/v2_*/{_table}')
for _table in SEEDS:
    register_csv('seed_' + _table, f'{ROOT}/seeds/{_table}')


@dp.table(name=name('bronze', 'seed_event_filters'), table_properties={'delta.appendOnly': 'true'})
def filters_bronze():
    return (spark.readStream.format('cloudFiles').option('cloudFiles.format', 'json')  # noqa: F821
            .schema('config STRING, _rescued_data STRING').load(f'{ROOT}/seeds/event_filters')
            .select('*', F.col('_metadata.file_path').alias('_source_file'),
                    F.current_timestamp().alias('ingest_ts')))


# Window predicates must be evaluated in a projection before a filter.
def latest_snapshot(frame):
    return (frame.withColumn('_latest', F.max('ingest_ts').over(Window.partitionBy()))
            .where('ingest_ts = _latest').drop('_latest'))


def latest_seed(table):
    return latest_snapshot(read('bronze', 'seed_' + table))


def current_gtfs(table):
    feeds = (read('bronze', 'gtfs_feed_info').groupBy('feed_version')
             .agg(F.max('ingest_ts').alias('_seen'))
             .orderBy(F.desc('_seen'), F.desc('feed_version')).limit(1).select('feed_version'))
    return read('bronze', 'gtfs_' + table).join(feeds, 'feed_version').dropDuplicates()


GTFS_CASTS = {
    'routes': {'route_type': 'int', 'route_sort_order': 'int'},
    'stops': {'stop_lat': 'double', 'stop_lon': 'double', 'location_type': 'int',
              'wheelchair_boarding': 'int'},
    'trips': {'direction_id': 'int', 'wheelchair_accessible': 'int', 'bikes_allowed': 'int'},
    'stop_times': {'stop_sequence': 'int', 'pickup_type': 'int', 'drop_off_type': 'int',
                   'timepoint': 'int', 'shape_dist_traveled': 'double'},
    'shapes': {'shape_pt_lat': 'double', 'shape_pt_lon': 'double', 'shape_pt_sequence': 'int',
               'shape_dist_traveled': 'double'},
    'calendar_dates': {'exception_type': 'int'},
}


def register_gtfs(table):
    @dp.materialized_view(name=name('silver', 'gtfs_' + table))
    def typed_gtfs():
        frame = current_gtfs(table)
        for column, kind in GTFS_CASTS[table].items():
            if column in frame.columns:
                frame = frame.withColumn(column, F.expr(f'try_cast(`{column}` AS {kind})'))
        if table == 'calendar_dates':
            frame = frame.withColumn('date', F.to_date('date', 'yyyyMMdd'))
        if table == 'stop_times':
            for column in ('arrival_time', 'departure_time'):
                seconds = (F.split(F.col(column), ':')[0].cast('int') * 3600
                           + F.split(F.col(column), ':')[1].cast('int') * 60
                           + F.split(F.col(column), ':')[2].cast('int'))
                frame = frame.withColumn(column + '_seconds', seconds)
            # Explicit aliases for downstream clients; original HH:MM:SS is preserved.
            frame = (frame.withColumn('arrival_secs', F.col('arrival_time_seconds'))
                     .withColumn('departure_secs', F.col('departure_time_seconds')))
        return frame


for _table in GTFS_CASTS:
    register_gtfs(_table)


@dp.materialized_view(name=name('silver', 'schedule_expanded'))
@dp.expect_or_drop('scheduled_time_present', 'sched_arrival_ts IS NOT NULL')
def schedule_expanded():
    return sql(f'''
      SELECT c.date AS service_date, t.trip_id, t.route_id, r.route_short_name,
             t.direction_id, t.shape_id, s.stop_id, s.stop_sequence,
             timestamp_seconds(unix_timestamp(to_utc_timestamp(
               to_timestamp(concat(c.date, ' 12:00:00')), 'America/Chicago'))
               + s.arrival_time_seconds - 43200) AS sched_arrival_ts,
             timestamp_seconds(unix_timestamp(to_utc_timestamp(
               to_timestamp(concat(c.date, ' 12:00:00')), 'America/Chicago'))
               + s.departure_time_seconds - 43200) AS sched_departure_ts,
             s.timepoint = 1 AS is_timepoint
      FROM {name('silver', 'gtfs_calendar_dates')} c
      JOIN {name('silver', 'gtfs_trips')} t ON c.service_id = t.service_id
      JOIN {name('silver', 'gtfs_stop_times')} s ON t.trip_id = s.trip_id
      JOIN {name('silver', 'gtfs_routes')} r ON t.route_id = r.route_id
      WHERE c.exception_type = 1
    ''')


DIRECTION = '''STRUCT<direction:STRUCT<key:STRING,name:STRING>, destination:STRING,
 lineColor:STRING, textColor:STRING, patternList:ARRAY<STRUCT<key:STRING,isDisplay:BOOLEAN>>,
 serviceInterruptionKeys:ARRAY<STRING>, upcomingServiceInterruptionKeys:ARRAY<STRING>>'''
ROUTE = f'STRUCT<key:STRING,name:STRING,shortName:STRING,directionList:ARRAY<{DIRECTION}>>'
ALERT = '''STRUCT<key:STRING,name:STRING,description:STRING,startDateUtc:STRING,endDateUtc:STRING,
 dailyStartTime:STRING,dailyEndTime:STRING>'''
BASE = f'''STRUCT<routes:ARRAY<{ROUTE}>,serviceInterruptions:ARRAY<{ALERT}>,
 upcomingServiceInterruptions:ARRAY<{ALERT}>>'''


@dp.temporary_view(name='core_latest_base')
def latest_base():
    return (read('bronze', 'tripspark_base').where('payload IS NOT NULL AND http_status = 200')
            .orderBy(F.desc('ingest_ts'), F.desc('_source_file')).limit(1)
            .select(F.from_json('payload', BASE).alias('base')))


@dp.materialized_view(name=name('silver', 'tripspark_routes'))
def tripspark_routes():
    routes = (spark.read.table('core_latest_base')  # noqa: F821
              .select(F.explode('base.routes').alias('r'))
              .select('r.*').withColumn('d', F.explode_outer('directionList'))
              .select(F.col('key').alias('route_key'), F.col('shortName').alias('route_short_name'),
                      F.col('name').alias('route_name'), F.col('d.lineColor').alias('color'),
                      F.col('d.direction.key').alias('direction_key'),
                      F.col('d.direction.name').alias('direction_name'),
                      F.col('d.destination').alias('destination'),
                      F.col('d.patternList').alias('patterns'),
                      F.col('d.serviceInterruptionKeys').alias('service_interruption_keys'),
                      F.col('d.upcomingServiceInterruptionKeys').alias('upcoming_interruption_keys')))
    gtfs = read('silver', 'gtfs_routes').select('route_short_name', 'route_id', 'route_long_name')
    return routes.join(gtfs, 'route_short_name', 'left')


@dp.materialized_view(name=name('silver', 'tripspark_route_mismatches'))
def route_mismatches():
    return (read('silver', 'tripspark_routes').where('route_id IS NULL')
            .select('route_key', 'route_short_name', 'route_name').distinct()
            .withColumn('reason', F.lit('TripSpark short name absent from current GTFS')))


@dp.materialized_view(name=name('silver', 'service_alerts'))
def service_alerts():
    alerts = (spark.read.table('core_latest_base')  # noqa: F821
              .select(F.explode(F.concat('base.serviceInterruptions',
                                        'base.upcomingServiceInterruptions')).alias('a'))
              .select('a.*').dropDuplicates(['key']))
    route_refs = (read('silver', 'tripspark_routes')
                  .withColumn('alert_id', F.explode(F.array_union('service_interruption_keys',
                                                                'upcoming_interruption_keys')))
                  .groupBy('alert_id').agg(F.collect_set('route_short_name').alias('routes')))
    stops = read('silver', 'gtfs_stops').select('stop_code', 'stop_id')
    closed = (alerts.select(F.col('key').alias('alert_id'), F.explode_outer(
        F.udf(closed_stop_codes, 'array<string>')('description')).alias('stop_code'))
        .join(stops, 'stop_code', 'left').groupBy('alert_id')
        .agg(F.collect_set('stop_id').alias('closed_stops')))
    return (alerts.select(F.col('key').alias('alert_id'),
                          F.col('startDateUtc').cast('timestamp').alias('start_ts'),
                          F.col('endDateUtc').cast('timestamp').alias('end_ts'),
                          F.struct(F.col('dailyStartTime').alias('start_local'),
                                   F.col('dailyEndTime').alias('end_local'),
                                   F.lit('America/Chicago').alias('timezone')).alias('daily_window'),
                          F.col('name').alias('title'),
                          F.udf(strip_html, 'string')('description').alias('text'))
            .join(route_refs, 'alert_id', 'left').join(closed, 'alert_id', 'left')
            .fillna({'title': '', 'text': ''})
            .withColumn('routes', F.coalesce('routes', F.array().cast('array<string>')))
            .withColumn('closed_stops', F.coalesce('closed_stops', F.array().cast('array<string>'))))


VEHICLE = '''STRUCT<key:STRING,name:STRING,routeKey:STRING,directionKey:STRING,directionName:STRING,
 isExtraTrip:BOOLEAN,passengersOnboard:INT,passengerCapacity:INT,
 location:STRUCT<lastGpsDate:STRING,latitude:DOUBLE,longitude:DOUBLE,speed:DOUBLE,heading:DOUBLE>>'''
VEHICLES = f'''ARRAY<STRUCT<routeKey:STRING, vehiclesByDirections:
 ARRAY<STRUCT<directionKey:STRING,vehicles:ARRAY<{VEHICLE}>>>>>'''
QUALITY = {
    'vehicle_identity': 'vehicle_id IS NOT NULL AND ts_gps IS NOT NULL',
    'local_bbox': 'lat BETWEEN 30.50 AND 30.80 AND lon BETWEEN -96.60 AND -96.20',
    'valid_apc': 'capacity > 0 AND onboard >= 0 AND onboard <= 1.5 * capacity',
    'fresh_gps': 'unix_timestamp(ingest_ts) - unix_timestamp(ts_gps) < 300 '
                 'AND unix_timestamp(ingest_ts) - unix_timestamp(ts_gps) >= -60',
    'route_present': 'route_key IS NOT NULL',
}


@dp.materialized_view(name=name('silver', 'vehicle_pings_candidates'))
@dp.expect_all(QUALITY)
def ping_candidates():
    raw = (read('bronze', 'tripspark_vehicles')
           .withColumn('r', F.explode(F.from_json('payload', VEHICLES)))
           .withColumn('d', F.explode('r.vehiclesByDirections'))
           .withColumn('v', F.explode('d.vehicles')))
    pings = raw.select(
        F.col('v.key').alias('vehicle_id'), F.col('v.name').alias('vehicle_name'),
        F.udf(gps_time_utc, 'timestamp')('v.location.lastGpsDate', 'ingest_ts').alias('ts_gps'),
        'ingest_ts', 'poll_id', F.coalesce('v.routeKey', 'r.routeKey').alias('route_key'),
        F.coalesce('v.directionKey', 'd.directionKey').alias('direction_key'),
        F.col('v.directionName').alias('_direction_name'),
        F.col('v.location.latitude').alias('lat'), F.col('v.location.longitude').alias('lon'),
        F.col('v.location.speed').alias('speed'), F.col('v.location.heading').alias('heading'),
        F.col('v.passengersOnboard').alias('onboard'),
        F.col('v.passengerCapacity').alias('capacity'),
        F.col('v.isExtraTrip').alias('is_extra_trip'),
        F.regexp_extract('_source_file', r'/vehicles_\d{8}T\d{2}(?:\d{2})?_([^/]+)\.jsonl(?:\.gz)?$', 1)
        .alias('source_host'), '_source_file',
    )
    metadata = read('silver', 'tripspark_routes').select(
        'route_key', 'direction_key', 'route_short_name', 'route_name', 'direction_name')
    return (pings.join(metadata, ['route_key', 'direction_key'], 'left')
            .withColumn('direction_name', F.coalesce('_direction_name', 'direction_name'))
            .drop('_direction_name').withColumn('load_factor', F.expr('try_divide(onboard, capacity)'))
            .withColumn('data_origin', F.lit('observed'))
            .withColumn('_normalization_revision', F.lit(CORE_REVISION)))


@dp.materialized_view(name=name('silver', 'vehicle_pings_quarantine'))
def quarantine():
    frame = read('silver', 'vehicle_pings_candidates')
    return frame.withColumn('failed_expectations', F.filter(
        F.array(*[F.when(~F.coalesce(F.expr(condition), F.lit(False)), F.lit(key))
                  for key, condition in QUALITY.items()]), lambda x: x.isNotNull()))\
        .where('size(failed_expectations) > 0')


@dp.materialized_view(name=name('silver', 'vehicle_pings'))
@dp.expect_all_or_drop(QUALITY)
def vehicle_pings():
    frame = read('silver', 'vehicle_pings_candidates')
    frame = frame.where(' AND '.join(f'coalesce(({x}), false)' for x in QUALITY.values()))
    frame = (frame.withColumn('_rank', F.row_number().over(Window.partitionBy('vehicle_id', 'ts_gps')
             .orderBy('ingest_ts', 'poll_id', '_source_file'))).where('_rank = 1').drop('_rank'))
    # 203 GTFS stops: exact broadcast nearest-neighbour avoids sparse-cell misses.
    stops = read('silver', 'gtfs_stops').select('stop_id', 'stop_lat', 'stop_lon')
    distances = frame.crossJoin(F.broadcast(stops)).withColumn('_a',
        F.pow(F.sin(F.radians(F.col('stop_lat') - F.col('lat')) / 2), 2)
        + F.cos(F.radians('lat')) * F.cos(F.radians('stop_lat'))
        * F.pow(F.sin(F.radians(F.col('stop_lon') - F.col('lon')) / 2), 2))
    distances = distances.withColumn('nearest_stop_dist_m',
        12742000 * F.asin(F.sqrt(F.least(F.lit(1.0), F.col('_a')))))
    return (distances.withColumn('_rank', F.row_number().over(
        Window.partitionBy('vehicle_id', 'ts_gps').orderBy('nearest_stop_dist_m', 'stop_id')))
        .where('_rank = 1').withColumnRenamed('stop_id', 'nearest_stop_id')
        .withColumn('h3_r10', F.expr('h3_longlatash3(lon, lat, 10)'))
        .drop('_rank', '_a', 'stop_lat', 'stop_lon', '_source_file', '_normalization_revision'))


@dp.materialized_view(name=name('silver', 'vehicle_latest'))
def vehicle_latest():
    return (read('silver', 'vehicle_pings').where('ts_gps >= current_timestamp() - INTERVAL 10 MINUTES')
            .withColumn('_rank', F.row_number().over(Window.partitionBy('vehicle_id')
                                                   .orderBy(F.desc('ts_gps'), F.desc('ingest_ts'))))
            .where('_rank = 1').drop('_rank'))


EVENT_SCHEMA = '''event_id STRING,title STRING,start_ts TIMESTAMP,end_ts TIMESTAMP,
 is_all_day BOOLEAN,location_title STRING,venue_key STRING,lat DOUBLE,lon DOUBLE,campus STRING,
 category STRING,event_types ARRAY<STRING>,audience ARRAY<STRING>,group_title STRING,
 rsvp_total INT,is_athletics BOOLEAN,is_football_home BOOLEAN,est_attendance INT,
 significance DOUBLE,is_canceled BOOLEAN,reason STRING'''


@dp.materialized_view(name=name('silver', 'events_evaluated'))
def events_evaluated():
    venues = latest_seed('venues').agg(F.to_json(F.collect_list(F.struct(
        'venue_key', 'display_name', 'aliases', 'lat', 'lon', 'capacity', 'campus'))).alias('venues'))
    config = latest_seed('event_filters').orderBy(F.desc('_source_file')).limit(1).select('config')
    return (read('bronze', 'events').where('payload IS NOT NULL').crossJoin(venues).crossJoin(config)
            .select(F.from_json(F.udf(normalize_event, 'string')('payload', 'venues', 'config'),
                                EVENT_SCHEMA).alias('e'), 'ingest_ts')
            .select('e.*', 'ingest_ts', F.lit(CORE_REVISION).alias('_normalization_revision')))


@dp.materialized_view(name=name('silver', 'events_filter_stats'))
def events_filter_stats():
    frame = read('silver', 'events_evaluated')
    by_reason = frame.groupBy('reason').agg(F.count('*').alias('raw_count'))
    return (by_reason.withColumn('kept_count', F.when(F.col('reason') == 'kept', F.col('raw_count'))
                                 .otherwise(F.lit(0)))
            .withColumn('dropped_count', F.col('raw_count') - F.col('kept_count')))


@dp.materialized_view(name=name('silver', 'events'))
@dp.expect('event_has_start', 'start_ts IS NOT NULL')
def events():
    calendar_latest = (read('silver', 'events_evaluated')
                .withColumn('_rank', F.row_number().over(Window.partitionBy('event_id', 'start_ts')
                                                       .orderBy(F.desc('ingest_ts'))))
                .where('_rank = 1').drop('_rank', 'ingest_ts'))
    observed = calendar_latest.where("reason = 'kept'").drop('reason', '_normalization_revision')
    venues = latest_seed('venues').select('venue_key', 'display_name', 'lat', 'lon', 'capacity')
    sports = []
    for sport, table, duration in [('Football', 'football_home', 4),
                                    ('Basketball', 'basketball_home', 3)]:
        seed = latest_seed(table).where("lower(home_away_neutral) = 'home'")
        team = F.col('team') if sport == 'Basketball' else F.lit('football')
        seed = seed.withColumn('team_name', team).join(venues, 'venue_key', 'left')
        # Unknown kickoff: all-day local event, explicitly distinguish from a known time.
        seed = seed.withColumn('_all_day', F.col('kickoff_local').isNull() | (F.col('kickoff_local') == ''))
        seed = seed.withColumn('_start', F.to_utc_timestamp(F.to_timestamp(F.concat_ws(' ', 'date',
            F.when(F.col('_all_day'), F.lit('00:00')).otherwise(F.col('kickoff_local')))), 'America/Chicago'))
        seed = seed.withColumn('_end', F.when(F.col('_all_day'), F.to_utc_timestamp(
            F.to_timestamp(F.concat(F.date_add(F.to_date('date'), 1), F.lit(' 00:00:00'))),
            'America/Chicago')).otherwise(F.col('_start') + F.expr(f'INTERVAL {duration} HOURS')))
        sports.append(seed.select(
            F.concat_ws(':', F.lit('athletics'), F.lit(sport.lower()), 'team_name', 'date').alias('event_id'),
            F.concat_ws(' ', F.lit('Texas A&M'), 'team_name', F.lit('vs'), 'opponent').alias('title'),
            F.col('_start').alias('start_ts'), F.col('_end').alias('end_ts'),
            F.col('_all_day').alias('is_all_day'), F.col('display_name').alias('location_title'),
            'venue_key', F.col('lat').cast('double').alias('lat'), F.col('lon').cast('double').alias('lon'),
            F.lit('Bryan-College Station').alias('campus'), F.lit('Sports & Athletics').alias('category'),
            F.array(F.lit('Sports & Athletics')).alias('event_types'),
            F.array(F.lit('Students'), F.lit('Visitors')).alias('audience'),
            F.lit('Aggie Athletics').alias('group_title'), F.lit(None).cast('int').alias('rsvp_total'),
            F.lit(True).alias('is_athletics'), F.lit(sport == 'Football').alias('is_football_home'),
            F.col('capacity').cast('int').alias('est_attendance'),
            (F.log1p(F.col('capacity').cast('double')) * 2.5 * 1.5).alias('significance'),
            F.lit(False).alias('is_canceled')))
    seeds = sports[0].unionByName(sports[1])
    # Add only missing sport/venue/local-date instances, not duplicate safety-net rows.
    keys = (calendar_latest.where("is_athletics AND (reason = 'kept' OR is_canceled)").select('venue_key',
            F.to_date(F.from_utc_timestamp('start_ts', 'America/Chicago')).alias('_date'),
            F.when(F.lower('title').contains('football'), F.lit('football'))
            .when(F.lower('title').contains('basketball'), F.lit('basketball')).alias('_sport'),
            F.when(F.lower('title').contains('football'), F.lit('football'))
            .when(F.lower('title').contains('women'), F.lit('women')).otherwise(F.lit('men')).alias('_team')))
    seeds = (seeds.withColumn('_date', F.to_date(F.from_utc_timestamp('start_ts', 'America/Chicago')))
             .withColumn('_sport', F.when(F.col('is_football_home'), F.lit('football'))
                         .otherwise(F.lit('basketball')))
             .withColumn('_team', F.when(F.col('is_football_home'), F.lit('football'))
                         .when(F.col('event_id').contains(':women:'), F.lit('women')).otherwise(F.lit('men')))
             .join(keys, ['venue_key', '_date', '_sport', '_team'], 'left_anti').drop('_date', '_sport', '_team'))
    return observed.unionByName(seeds)


@dp.materialized_view(name=name('silver', 'weather_hourly'))
@dp.expect_or_drop('weather_hour_present', 'ts_hour IS NOT NULL')
def weather_hourly():
    schema = '''STRUCT<utc_offset_seconds:INT,hourly:STRUCT<time:ARRAY<STRING>,
      temperature_2m:ARRAY<DOUBLE>,apparent_temperature:ARRAY<DOUBLE>,precipitation:ARRAY<DOUBLE>,
      rain:ARRAY<DOUBLE>,wind_speed_10m:ARRAY<DOUBLE>,weather_code:ARRAY<INT>>>'''
    om = (read('bronze', 'weather_openmeteo').withColumn('p', F.from_json('payload', schema))
          .select('*', F.posexplode('p.hourly.time').alias('i', '_time')))
    om = om.select(
        F.timestamp_seconds(F.unix_timestamp(F.col('_time').cast('timestamp'))
                            - F.coalesce(F.col('p.utc_offset_seconds'), F.lit(0))).alias('ts_hour'),
        *[F.get(F.col('p.hourly.' + source), F.col('i')).alias(target) for source, target in [
            ('temperature_2m', 'temp_c'), ('apparent_temperature', 'apparent_c'),
            ('precipitation', 'precip_mm'), ('rain', 'rain_mm'), ('wind_speed_10m', 'wind_kph'),
            ('weather_code', 'weather_code')]],
        F.concat(F.lit('openmeteo_'), F.col('kind')).alias('source'),
        (F.col('kind') == 'forecast').alias('is_forecast'),
        F.when(F.col('kind') == 'forecast', F.col('ingest_ts')).alias('forecast_issued_ts'),
        'ingest_ts', F.col('_source_file').alias('_tie'),
    )
    quantity = 'STRUCT<value:DOUBLE,unitCode:STRING>'
    nws_schema = f'''STRUCT<features:ARRAY<STRUCT<properties:STRUCT<timestamp:STRING,
        temperature:{quantity},heatIndex:{quantity},windChill:{quantity},windSpeed:{quantity},
        precipitationLastHour:{quantity}>>>>'''
    nws = (read('bronze', 'weather_nws').withColumn('p', F.from_json('payload', nws_schema))
           .withColumn('obs', F.explode('p.features')).select('obs.properties.*', 'ingest_ts', '_source_file'))
    nws = nws.select(
        F.date_trunc('hour', F.col('timestamp').cast('timestamp')).alias('ts_hour'),
        F.col('temperature.value').alias('temp_c'),
        F.coalesce('heatIndex.value', 'windChill.value', 'temperature.value').alias('apparent_c'),
        (F.col('precipitationLastHour.value') * F.when(F.col('precipitationLastHour.unitCode') == 'wmoUnit:m',
            F.lit(1000)).otherwise(F.lit(1))).alias('precip_mm'),
        F.lit(None).cast('double').alias('rain_mm'), F.col('windSpeed.value').alias('wind_kph'),
        F.lit(None).cast('int').alias('weather_code'), F.lit('nws').alias('source'),
        F.lit(False).alias('is_forecast'), F.lit(None).cast('timestamp').alias('forecast_issued_ts'),
        'ingest_ts', F.col('timestamp').alias('_tie'))
    return (om.unionByName(nws).withColumn('_rank', F.row_number().over(
        Window.partitionBy('ts_hour', 'source').orderBy(F.desc('ingest_ts'), F.desc('_tie'))))
        .where('_rank = 1').drop('_rank', 'ingest_ts', '_tie'))


@dp.materialized_view(name=name('silver', 'academic_days'))
def academic_days():
    frame = latest_seed('academic_days').withColumn('date', F.to_date('date'))
    for column in ('is_class_day', 'is_holiday', 'is_break', 'is_reading_day', 'is_finals', 'is_commencement'):
        frame = frame.withColumn(column, F.col(column) == '1')
    for column in ('week_of_term', 'days_to_finals'):
        frame = frame.withColumn(column, F.expr(f'try_cast({column} AS INT)'))
    return frame


@dp.materialized_view(name=name('silver', 'class_periods'))
def class_periods():
    frame = latest_seed('class_periods')
    for column in ('start_time', 'end_time'):
        frame = frame.withColumn(column + '_seconds', F.split(F.col(column), ':')[0].cast('int') * 3600
                                 + F.split(F.col(column), ':')[1].cast('int') * 60)
    return frame


@dp.materialized_view(name=name('silver', 'ntd_monthly'))
def ntd_monthly():
    frame = (read('bronze', 'ntd_monthly')
             .select('date', 'ntd_id', 'agency', 'mode', 'tos', 'upt', 'vrm', 'vrh', 'voms', 'ingest_ts')
             .withColumn('month', F.to_date(F.date_trunc('month', F.col('date').cast('timestamp')))))
    for column, kind in [('upt', 'bigint'), ('vrm', 'double'), ('vrh', 'double'), ('voms', 'int')]:
        frame = frame.withColumn(column, F.expr(f'try_cast({column} AS {kind})'))
    return (frame.withColumn('_rank', F.row_number().over(Window.partitionBy('month', 'ntd_id', 'mode', 'tos')
             .orderBy(F.desc('ingest_ts')))).where('_rank = 1').drop('_rank', 'date'))
