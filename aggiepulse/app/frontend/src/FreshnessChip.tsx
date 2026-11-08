import {useEffect, useState} from 'react';
import {ageLabel, formatDateTime, freshnessLevel, parseUtc} from './format';
import type {Freshness} from './format';

export default function FreshnessChip() {
  const [freshness, setFreshness] = useState<Freshness>();
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    const poll = async () => {
      try {
        const response = await fetch('/api/meta', {signal: controller.signal});
        if (!response.ok) throw new Error('Metadata unavailable');
        const data = await response.json();
        if (!controller.signal.aborted) { setFreshness(data.freshness); setError(false); }
      } catch { if (!controller.signal.aborted) setError(true); }
    };
    void poll(); const timer = setInterval(poll, 15000);
    return () => { controller.abort(); clearInterval(timer); };
  }, []);
  const scoring = parseUtc(freshness?.last_scoring_ts);
  const level = error ? 'red' : freshness ? freshnessLevel(freshness) : 'amber';
  return <div className={`freshness freshness--${level}`} role="status" aria-live="polite">
    <strong>{error ? 'Status unavailable' : level === 'ok' ? 'Data current' : level === 'red' ? 'Data stale / unavailable' : 'Data delayed'}</strong>
    <span>Bus {ageLabel(freshness?.bus_snapshot_age_s)} · Scored {scoring ? `${formatDateTime(scoring)} CT` : 'unknown'} · Pipeline lag {ageLabel(freshness?.pipeline_lag_s)}</span>
  </div>;
}
