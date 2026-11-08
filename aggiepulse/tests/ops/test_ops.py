import json
import plistlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/ops'))
from freeze import freeze, restore_check
from health import collector_failed, run
from runtime import identifier, literal


@pytest.mark.parametrize(('expected', 'age', 'failed'), [
    (True, 30, False), (True, 30.01, True), (True, None, True),
    (False, None, False), (False, 60, False),
])
def test_collector_service_gate(expected, age, failed):
    assert collector_failed(expected, age) == failed


def test_sql_inputs():
    assert identifier('transit.gold.a') == '`transit`.`gold`.`a`'
    assert literal("a'b") == "'a\\'b'"
    with pytest.raises(ValueError):
        identifier('transit; DROP SCHEMA gold')


def test_snapshot_versions_and_restore_without_source_mutation():
    statements = []
    saved = {}
    def sql(query):
        statements.append(query)
        if 'information_schema' in query:
            return [{'table_schema': 'silver', 'table_name': 'pings', 'table_type': 'MATERIALIZED_VIEW'},
                    {'table_schema': 'gold', 'table_name': 'predictions', 'table_type': 'MANAGED'}]
        if query.startswith('DESCRIBE'):
            return [{'version': 7}]
        if query.startswith('SELECT to_json'):
            return [{'versions': json.dumps({'transit.gold.predictions': 7}),
                     'replay': json.dumps({'transit.gold.predictions': 'transit.gold.predictions'})}]
        if query.startswith('SELECT count'):
            return [{'n': 4}]
        return []
    class Client:
        def get_model_version_by_alias(self, name, alias):
            return SimpleNamespace(version='2', run_id='run-123')
    saved = freeze(sql, "test's snapshot", client=Client())
    assert saved['tables'] == 2 and len(saved['model_versions']) == 3
    assert any('CREATE TABLE `transit`.`ml`.`snapshot_' in q for q in statements)
    assert not any('OVERWRITE' in q or 'RESTORE TABLE' in q for q in statements)
    assert restore_check(sql, saved['snapshot_id'])['checked'] == 1
    assert 'VERSION AS OF 7' in statements[-1]


def test_health_persists_before_raising():
    writes = []
    def sql(query):
        writes.append(query)
        if query.startswith('WITH'):
            return {'service_expected': True, 'collector_age_min': 31.}.copy(),
        return []
    with pytest.raises(RuntimeError, match='Collector silent'):
        run(sql)
    assert writes[-1].startswith('INSERT INTO') and "'FAIL'" in writes[-1]


def test_launchd_dry_run_has_correct_schedule_and_no_load():
    result = subprocess.run(['bash', str(ROOT/'scripts/ops/install_launchd.sh'), '--dry-run'],
                            capture_output=True, text=True, check=True)
    xml = result.stdout[result.stdout.index('<?xml'):]
    plist = plistlib.loads(xml.encode())
    assert plist['Label'] == 'com.aggiepulse.daily-ingest'
    assert plist['StartCalendarInterval'] == {'Hour': 6, 'Minute': 0}
    assert plist['RunAtLoad'] is False
    assert plist['ProgramArguments'][1].endswith('/scripts/platform/run_daily_ingest.sh')
    assert 'no immediate run' in result.stdout


def test_scheduled_serverless_jobs_and_failure_email():
    import yaml

    jobs = yaml.safe_load((ROOT/'resources/jobs_ml.yml').read_text())['resources']['jobs']
    train = jobs['ml_train']
    assert train['schedule']['quartz_cron_expression'] == '0 30 3 * * ?'
    assert train['schedule']['timezone_id'] == 'America/Chicago'
    tasks = {task['task_key']: task for task in train['tasks']}
    assert tasks['train']['depends_on'] == [{'task_key': 'refresh_ml_data'}]
    assert tasks['evaluate']['depends_on'] == [{'task_key': 'train'}]
    assert tasks['register']['depends_on'] == [{'task_key': 'evaluate'}]
    health = jobs['ops_health']
    assert health['email_notifications']['on_failure'] == ['<redacted-email>']
    assert health['schedule']['quartz_cron_expression'] == '0 12/15 * * * ?'
    assert health['tasks'][0]['environment_key'] == 'ops'
    assert health['environments'][0]['spec']['environment_version'] == '4'


def test_missing_upstream_table_records_error_and_fails():
    statements = []
    def sql(query):
        statements.append(query)
        if query.lstrip().startswith('WITH'):
            raise RuntimeError('upstream table missing')
        return []
    with pytest.raises(RuntimeError, match='upstream table missing'):
        run(sql)
    assert statements[0].startswith('CREATE TABLE')
    assert statements[-1].startswith('INSERT INTO') and "'ERROR'" in statements[-1]
