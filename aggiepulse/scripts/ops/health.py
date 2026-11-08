"""Persist health before failing a silent-collector run during scheduled service."""
import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, sys.argv[sys.argv.index('--ops-root')+1] if '--ops-root' in sys.argv
                else str(Path(globals().get('__file__', sys.argv[0])).resolve().parent))

from runtime import SQL, identifier, literal


def collector_failed(service_expected, collector_age_min):
    return bool(service_expected and (collector_age_min is None or collector_age_min > 30))


def run(sql, catalog='transit'):
    cat = identifier(catalog)
    table = f'{cat}.gold.ops_health'
    sql(f'''CREATE TABLE IF NOT EXISTS {table} (run_id STRING, checked_ts TIMESTAMP,
        status STRING, service_expected BOOLEAN, collector_age_min DOUBLE, pipeline_lag_min DOUBLE,
        scoring_age_min DOUBLE, last_collector_ts TIMESTAMP, last_silver_ts TIMESTAMP,
        last_scoring_ts TIMESTAMP, apc_pings BIGINT, apc_stuck_minutes DOUBLE,
        apc_over_capacity_pings BIGINT, error_message STRING) USING DELTA''')
    # Route-day first/last scheduled arrivals define service, including long gaps.
    # Healthy polling is http_status=200 with payload, even when no buses are active.
    try:
        row = sql(f'''WITH service AS (
              SELECT route_id, service_date, min(sched_arrival_ts) start_ts,
                     max(sched_departure_ts) end_ts FROM {cat}.silver.schedule_expanded GROUP BY 1,2),
            bronze AS (SELECT max(ingest_ts) last_collector_ts FROM {cat}.bronze.tripspark_vehicles
                       WHERE http_status=200 AND payload IS NOT NULL),
            silver AS (SELECT max(ingest_ts) last_silver_ts FROM {cat}.silver.vehicle_pings),
            score AS (SELECT max(scoring_ts) last_scoring_ts FROM {cat}.gold.predictions),
            quality AS (SELECT coalesce(sum(pings),0) apc_pings, coalesce(sum(stuck_minutes),0) apc_stuck_minutes,
                        coalesce(sum(over_capacity_pings),0) apc_over_capacity_pings
                        FROM {cat}.silver.apc_quality
                        WHERE service_date = to_date(from_utc_timestamp(current_timestamp(),'America/Chicago')))
            SELECT current_timestamp() checked_ts,
              EXISTS(SELECT 1 FROM service WHERE current_timestamp() BETWEEN start_ts AND end_ts) service_expected,
              last_collector_ts, last_silver_ts, last_scoring_ts,
              (unix_timestamp(current_timestamp())-unix_timestamp(last_collector_ts))/60. collector_age_min,
              CASE WHEN last_silver_ts IS NULL THEN NULL ELSE
              greatest(0.,(unix_timestamp(last_collector_ts)-unix_timestamp(last_silver_ts))/60.) END pipeline_lag_min,
              (unix_timestamp(current_timestamp())-unix_timestamp(last_scoring_ts))/60. scoring_age_min,
              apc_pings, apc_stuck_minutes, apc_over_capacity_pings
            FROM bronze CROSS JOIN silver CROSS JOIN score CROSS JOIN quality''')[0]
    except Exception as exc:
        # A missing upstream table is still a persisted, failed health run.
        error = {'run_id': uuid.uuid4().hex, 'status': 'ERROR', 'error_message': str(exc)[:2000]}
        sql(f"INSERT INTO {table} (run_id, checked_ts, status, error_message) SELECT "
            f"{literal(error['run_id'])}, current_timestamp(), 'ERROR', {literal(error['error_message'])}")
        print(json.dumps(error))
        raise
    expected = row['service_expected'] is True or str(row['service_expected']).lower() == 'true'
    age = float(row['collector_age_min']) if row['collector_age_min'] is not None else None
    failed = collector_failed(expected, age)
    row.update(run_id=uuid.uuid4().hex, status='FAIL' if failed else 'OK')
    types = {'checked_ts': 'TIMESTAMP', 'service_expected': 'BOOLEAN',
             'last_collector_ts': 'TIMESTAMP', 'last_silver_ts': 'TIMESTAMP', 'last_scoring_ts': 'TIMESTAMP',
             'collector_age_min': 'DOUBLE', 'pipeline_lag_min': 'DOUBLE', 'scoring_age_min': 'DOUBLE',
             'apc_pings': 'BIGINT', 'apc_stuck_minutes': 'DOUBLE', 'apc_over_capacity_pings': 'BIGINT'}
    values = [f"CAST({literal(v) if v is not None else 'NULL'} AS {types.get(k, 'STRING')})"
              for k, v in row.items()]
    sql(f"INSERT INTO {table} ({','.join(identifier(k) for k in row)}) SELECT {','.join(values)}")
    print(json.dumps(row, indent=2, default=str))
    if failed:
        raise RuntimeError('Collector silent >30 minutes (or never observed) during scheduled service')
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ops-root')
    parser.add_argument('--catalog', default='transit')
    parser.add_argument('--spark', action='store_true')
    args = parser.parse_args()
    run(SQL(args.spark), args.catalog)


if __name__ == '__main__':
    main()
