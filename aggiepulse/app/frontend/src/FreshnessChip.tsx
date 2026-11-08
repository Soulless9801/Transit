import {ageLabel, friendlyDate, freshnessLevel, parseUtc} from './format';
import type {Freshness} from './format';
import {useApi} from './ui';

export default function FreshnessChip() {
  const meta = useApi<{freshness: Freshness}>('/api/meta',15000);
  const freshness = meta.data?.freshness;
  const next = parseUtc(freshness?.next_scheduled_ts);
  const level = meta.error ? 'red' : freshness ? freshnessLevel(freshness) : 'amber';
  return <div className={`freshness freshness--${level}`} role="status" aria-live="polite">
    <strong>{meta.loading ? 'Checking bus service…' : meta.error ? 'Live status is temporarily unavailable' : freshness?.in_service === false ? 'Buses are off for now' : level === 'ok' ? 'Bus data is up to date' : level === 'red' ? 'Live updates are unavailable' : 'Bus updates are delayed'}</strong>
    <span>{freshness?.in_service === false ? (next ? `Next service: ${friendlyDate(next)} CT` : 'Check back when service resumes') : freshness ? `Last bus update: ${ageLabel(freshness.bus_snapshot_age_s)} ago` : 'Bus times are Central time'}</span>
  </div>;
}
