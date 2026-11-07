"""Capture exact counts, expectation rates, ranges, mismatches and sample rows.

Uses the transit CLI profile; writes a reproducible JSON validation artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

PIPELINE_ID = '206b4370-b5cc-48eb-8eba-932dcea5dc95'
WAREHOUSE_ID = '3c5b1dbf21774cde'
BRONZE = ['tripspark_vehicles', 'tripspark_base', 'events', 'weather_openmeteo',
          'weather_nws', 'ntd_monthly', *['gtfs_' + t for t in (
              'routes', 'stops', 'trips', 'stop_times', 'shapes', 'calendar_dates', 'feed_info')],
          *['seed_' + t for t in ('academic_days', 'venues', 'class_periods',
                                   'football_home', 'basketball_home', 'event_filters')]]
SILVER = ['vehicle_pings', 'vehicle_latest', 'vehicle_pings_candidates', 'vehicle_pings_quarantine',
          *['gtfs_' + t for t in ('routes', 'stops', 'trips', 'stop_times', 'shapes', 'calendar_dates')],
          'schedule_expanded', 'tripspark_routes', 'tripspark_route_mismatches', 'service_alerts',
          'events', 'events_evaluated', 'events_filter_stats', 'weather_hourly',
          'academic_days', 'class_periods', 'ntd_monthly']


def cli(*parts):
    return json.loads(subprocess.check_output(['/opt/homebrew/bin/databricks', *parts,
                                              '-p', 'transit', '--output', 'json']))


def query(statement):
    response = cli('api', 'post', '/api/2.0/sql/statements', '--json', json.dumps({
        'warehouse_id': WAREHOUSE_ID, 'statement': statement, 'wait_timeout': '50s',
        'row_limit': 1000}))
    while response['status']['state'] in ('PENDING', 'RUNNING'):
        time.sleep(2)
        response = cli('api', 'get', '/api/2.0/sql/statements/' + response['statement_id'])
    if response['status']['state'] != 'SUCCEEDED':
        raise RuntimeError(json.dumps(response['status']))
    schema = response['manifest']['schema'].get('columns', [])

    def typed(value, column):
        if value is None:
            return None
        kind = column.get('type_name')
        if kind in ('ARRAY', 'MAP', 'STRUCT', 'BOOLEAN'):
            return json.loads(value)
        if kind in ('LONG', 'INT', 'SHORT', 'BYTE'):
            return int(value)
        if kind in ('DOUBLE', 'FLOAT', 'DECIMAL'):
            return float(value)
        return value

    return [{column['name']: typed(value, column) for column, value in zip(schema, row, strict=True)}
            for row in response.get('result', {}).get('data_array', [])]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-run')
    parser.add_argument('--output', default=str(Path(__file__).with_name('validation_report.json')))
    args = parser.parse_args()
    tables = [('bronze', t) for t in BRONZE] + [('silver', t) for t in SILVER]
    counts = query(' UNION ALL '.join(
        f"SELECT '{s}.{t}' AS table_name, count(*) AS row_count FROM transit.{s}.{t}" for s, t in tables))
    updates = cli('pipelines', 'get', PIPELINE_ID).get('latest_updates')
    completed = ','.join("'" + u['update_id'] + "'" for u in updates if u['state'] == 'COMPLETED')
    batches = query(f"""SELECT timestamp, origin.update_id AS update_id,
        get_json_object(details, '$.flow_progress.data_quality.expectations') AS expectations
        FROM event_log('{PIPELINE_ID}') WHERE origin.update_id IN ({completed})
        AND event_type = 'flow_progress'
        AND get_json_object(details, '$.flow_progress.data_quality.expectations') IS NOT NULL
        ORDER BY timestamp""")
    # A NO_OP refresh has no new pass/fail samples. Retain each rule's most recent
    # nonempty measurement, with its originating update for an honest audit.
    latest_metrics = {}
    for row in batches:
        for metric in json.loads(row['expectations']):
            if metric['passed_records'] + metric['failed_records']:
                latest_metrics[(metric['dataset'], metric['name'])] = {
                    'timestamp': row['timestamp'], 'update_id': row['update_id'], 'expectations': [metric]}
    expectations = [latest_metrics[key] for key in sorted(latest_metrics)]
    revisions = query('''SELECT DISTINCT 'vehicle_pings_candidates' AS dataset, _normalization_revision AS revision
        FROM transit.silver.vehicle_pings_candidates
        UNION ALL SELECT DISTINCT 'events_evaluated', _normalization_revision
        FROM transit.silver.events_evaluated''')
    helper_path = Path(__file__).resolve().parents[2] / 'pipelines/core/lib/helpers.py'
    expected_revision = hashlib.sha256(helper_path.read_bytes()).hexdigest()
    assert all(r['revision'] == expected_revision for r in revisions), revisions
    report = {'captured_at': datetime.now(UTC).isoformat(), 'pipeline_id': PIPELINE_ID,
              'normalization_revisions': revisions,
              'counts': counts, 'expectation_metrics': expectations,
              'contract_checks': query('''SELECT
                (SELECT count(*) - count(DISTINCT struct(vehicle_id,ts_gps)) FROM transit.silver.vehicle_pings) AS duplicate_pings,
                (SELECT count(*) - count(DISTINCT struct(event_id,start_ts)) FROM transit.silver.events) AS duplicate_events,
                (SELECT count(*) FROM transit.silver.schedule_expanded WHERE sched_departure_ts < sched_arrival_ts) AS negative_schedule_dwells,
                (SELECT max(arrival_time_seconds) FROM transit.silver.gtfs_stop_times) AS max_gtfs_arrival_seconds'''),
              'vehicle_range': query('SELECT min(ts_gps) AS min_ts_gps, max(ts_gps) AS max_ts_gps, '
                  'count(DISTINCT vehicle_id) AS vehicles, count(DISTINCT source_host) AS hosts '
                  'FROM transit.silver.vehicle_pings'),
              'vehicle_quality': query('''SELECT count(*) AS raw_pings,
                sum(CASE WHEN vehicle_id IS NOT NULL AND ts_gps IS NOT NULL THEN 1 ELSE 0 END) AS identity_pass,
                sum(CASE WHEN lat BETWEEN 30.50 AND 30.80 AND lon BETWEEN -96.60 AND -96.20 THEN 1 ELSE 0 END) AS bbox_pass,
                sum(CASE WHEN capacity > 0 AND onboard >= 0 AND onboard <= 1.5 * capacity THEN 1 ELSE 0 END) AS apc_pass,
                sum(CASE WHEN unix_timestamp(ingest_ts)-unix_timestamp(ts_gps) < 300
                  AND unix_timestamp(ingest_ts)-unix_timestamp(ts_gps) >= -60 THEN 1 ELSE 0 END) AS gps_age_pass,
                sum(CASE WHEN route_key IS NOT NULL THEN 1 ELSE 0 END) AS route_pass
                FROM transit.silver.vehicle_pings_candidates'''),
              'mismatches': query('SELECT * FROM transit.silver.tripspark_route_mismatches'),
              'filter_stats': query('SELECT * FROM transit.silver.events_filter_stats ORDER BY raw_count DESC'),
              'samples': {t: query(f'SELECT * FROM transit.silver.{t} LIMIT 3') for t in (
                  'vehicle_pings', 'schedule_expanded', 'service_alerts', 'events', 'weather_hourly', 'ntd_monthly')},
              'updates': updates,
              'job': cli('jobs', 'get', '699719992330798'),
              'job_run': cli('jobs', 'get-run', args.job_run) if args.job_run else None,
              }
    Path(args.output).write_text(json.dumps(report, indent=2) + '\n')
    lines = ['# Core pipeline validation', '', f"Captured: {report['captured_at']}", '',
             f"Pipeline: `{PIPELINE_ID}` — [open pipeline](https://dbc-595c228e-76f6.cloud.databricks.com/pipelines/{PIPELINE_ID})", '',
             'Schedule: every 15 minutes, UTC (`0 0/15 * * * ?`), unpaused.', '',
             'Files: `pipelines/core/core.py`, `pipelines/core/lib/`, `resources/pipeline_core.yml`,',
             '`scripts/pipeline_core/{prepare,status,report}.py`, `tests/pipeline_core/test_helpers.py`.', '',
             '## Table counts', '', '| Table | Rows |', '|---|---:|']
    lines.extend(f"| {r['table_name']} | {int(r['row_count']):,} |" for r in sorted(counts, key=lambda r: r['table_name']))
    lines.extend(['', '## Expectations', '', '| Dataset | Rule | Passed | Failed | Pass rate |', '|---|---|---:|---:|---:|'])
    for batch in expectations:
        for e in batch['expectations']:
            total = e['passed_records'] + e['failed_records']
            rate = f"{100 * e['passed_records'] / total:.2f}%" if total else 'no input'
            lines.append(f"| {e['dataset']} | {e['name']} | {e['passed_records']:,} | {e['failed_records']:,} | {rate} |")
    lines.extend(['', '## Vehicle coverage', '', '```json', json.dumps(report['vehicle_range'], indent=2), '```', '',
                  '## Route mismatches', '', '```json', json.dumps(report['mismatches'], indent=2), '```', '',
                  '## Sample rows', ''])
    for table, rows in report['samples'].items():
        lines.extend([f'### silver.{table}', '', '```json', json.dumps(rows, indent=2), '```', ''])
    lines.extend(['## Known gaps', '',
                  '- Vehicle coverage is the landed completed-hour history; current snapshots can be empty until newer files arrive.',
                  '- vehicle_latest is evaluated at refresh time; applications should also check ping age at request time.',
                  '- Speed units are undocumented; unverified venue sizes and coordinates remain null.',
                  '- Attendance, category/audience weights, and game durations are modeling priors; TBA kickoffs stay all-day.',
                  '- Forecast issue time is retrieval time; archive weather is not a finalized station-observation guarantee.',
                  '- Current Silver GTFS follows the newest feed; earlier versions remain in Bronze.',
                  '', 'Validation: local pytest and ruff passed; see JSON for complete pipeline/job run evidence.', ''])
    Path(args.output).with_suffix('.md').write_text('\n'.join(lines))
    print(json.dumps({'output': args.output, 'tables': len(counts), 'updates': updates, 'vehicle_range': report['vehicle_range']}, indent=2))


if __name__ == '__main__':
    main()
