"""Print concise pipeline progress and actionable errors using the transit profile."""
import argparse
import json
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('pipeline_id')
args = parser.parse_args()


def cli(*parts):
    return json.loads(subprocess.check_output(['/opt/homebrew/bin/databricks', *parts,
                                              '-p', 'transit', '--output', 'json']))


state = cli('pipelines', 'get', args.pipeline_id)
print(json.dumps({'state': state['state'], 'updates': state.get('latest_updates')}, indent=2))
events = cli('pipelines', 'list-pipeline-events', args.pipeline_id, '--max-results', '100')
for event in events:
    if event['level'] == 'ERROR' and event.get('origin', {}).get('update_id') == state['latest_updates'][0]['update_id']:
        print(json.dumps({'timestamp': event['timestamp'], 'message': event.get('message'),
                          'error': event.get('error')}, indent=2))
