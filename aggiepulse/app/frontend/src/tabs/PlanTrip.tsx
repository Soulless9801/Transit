import {useMemo, useState} from 'react';
import {campusHour, pct, resolveStop, shiftLabel, stopOptions} from '../format';
import type {StopOption} from '../format';
import {Badge, PageHeader, Status, useApi} from '../ui';

type GeoJsonStops = {
  type?: string;
  features: {
    properties: {
      stop_id: string;
      stop_code?: string;
      stop_name: string;
      routes?: string[];
    };
  }[];
};

type PlanOption = {
  route: string;
  direction: string;
  shift: -10 | 0 | 10 | 20;
  wait_min: number;
  ride_min: number;
  walk_min: number;
  crowding: number;
  score: number;
  reason: string;
  source: string;
};

export default function PlanTrip() {
  const stopsApi = useApi<GeoJsonStops>('/api/stops');
  const stops = stopsApi.data ?? {features: []};
  const options = useMemo(() => stopOptions(stops.features ?? []), [stops]);

  const [fromText, setFromText] = useState('');
  const [toText, setToText] = useState('');
  const [fromError, setFromError] = useState('');
  const [toError, setToError] = useState('');
  const [hour, setHour] = useState<number>(() => Math.max(5, Math.min(23, campusHour())));

  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [apiError, setApiError] = useState('');
  const [results, setResults] = useState<PlanOption[]>([]);

  const handleSwap = () => {
    setFromText(toText);
    setToText(fromText);
    setFromError('');
    setToError('');
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    const originStop: StopOption | null = resolveStop(options, fromText);
    const destStop: StopOption | null = resolveStop(options, toText);

    let hasError = false;
    let nextFromError = '';
    let nextToError = '';

    if (!originStop) {
      nextFromError = 'Choose a stop from the list.';
      hasError = true;
    }
    if (!destStop) {
      nextToError = 'Choose a stop from the list.';
      hasError = true;
    }
    if (originStop && destStop && originStop.id === destStop.id) {
      nextToError = 'Origin and destination must differ.';
      hasError = true;
    }

    setFromError(nextFromError);
    setToError(nextToError);

    if (hasError || !originStop || !destStop) {
      return;
    }

    setLoading(true);
    setSearched(true);
    setApiError('');

    try {
      const res = await fetch(
        `/api/plan?origin=${encodeURIComponent(originStop.id)}&destination=${encodeURIComponent(destStop.id)}&hour=${hour}`
      );
      if (!res.ok) throw new Error(`Could not load /api/plan (${res.status})`);
      const data: PlanOption[] = await res.json();
      setResults(data);
    } catch (err) {
      setApiError(String(err instanceof Error ? err.message : err));
      setResults([]);
    } finally {
      setLoading(false);
    }
  };

  const displayedOptions = results.slice(0, 6);

  return (
    <main className="dashboard" aria-labelledby="page-title">
      <PageHeader
        title="Plan my trip"
        lede="Compare direct routes between two stops, including stops within a 400 m walk. Times are schedule estimates; crowding is predicted."
      />
      <Status loading={stopsApi.loading} error={stopsApi.error || apiError} />

      <datalist id="stop-options">
        {options.map(o => (
          <option key={o.id} value={o.label} />
        ))}
      </datalist>

      <form
        onSubmit={handleSubmit}
        noValidate
        style={{display: 'flex', flexDirection: 'column', gap: '16px', maxWidth: '480px', margin: '24px 0'}}
      >
        <label className="field">
          From
          <input
            list="stop-options"
            autoComplete="off"
            placeholder="Type a stop name"
            value={fromText}
            onChange={e => {
              setFromText(e.target.value);
              if (fromError) setFromError('');
            }}
            aria-invalid={fromError ? true : undefined}
            aria-describedby={fromError ? 'from-error' : undefined}
          />
          {fromError && (
            <p className="field-error" id="from-error">
              Error: {fromError}
            </p>
          )}
        </label>

        <button
          type="button"
          className="btn btn--ghost"
          aria-label="Swap origin and destination"
          onClick={handleSwap}
          style={{alignSelf: 'flex-start'}}
        >
          Swap
        </button>

        <label className="field">
          To
          <input
            list="stop-options"
            autoComplete="off"
            placeholder="Type a stop name"
            value={toText}
            onChange={e => {
              setToText(e.target.value);
              if (toError) setToError('');
            }}
            aria-invalid={toError ? true : undefined}
            aria-describedby={toError ? 'to-error' : undefined}
          />
          {toError && (
            <p className="field-error" id="to-error">
              Error: {toError}
            </p>
          )}
        </label>

        <label className="field">
          Departure hour
          <select value={hour} onChange={e => setHour(Number(e.target.value))}>
            {Array.from({length: 19}, (_, i) => i + 5).map(h => (
              <option key={h} value={h}>
                {`${h % 12 || 12}:00 ${h < 12 ? 'AM' : 'PM'}`}
              </option>
            ))}
          </select>
        </label>

        <button
          type="submit"
          className="btn"
          disabled={loading}
          style={{alignSelf: 'flex-start'}}
        >
          {loading ? 'Searching…' : 'Find options'}
        </button>
      </form>

      {searched && (
        <section aria-live="polite">
          <h2>Options</h2>
          {loading ? (
            <p role="status" className="status-msg">
              Searching…
            </p>
          ) : apiError ? null : displayedOptions.length === 0 ? (
            <p>
              No direct route connects these stops, even with a 400 m walk. Try a nearby stop.
            </p>
          ) : (
            <>
              <ol className="cards" aria-label="Trip options, best first" style={{listStyle: 'none', padding: 0}}>
                {displayedOptions.map((opt, i) => (
                  <li key={`${opt.route}-${opt.direction}-${opt.shift}-${i}`}>
                    <article>
                      <h3>
                        Route {opt.route}
                        {i === 0 && <> <Badge kind="BEST OPTION" /></>}
                      </h3>
                      <p className="lede">{shiftLabel(opt.shift)}</p>
                      <p>
                        <span className="big-stat">{Math.round(opt.score)} min</span>{' '}
                        <span className="stat-label">effective trip time</span>
                      </p>
                      <dl className="facts">
                        <dt>Wait</dt>
                        <dd>{opt.wait_min} min</dd>
                        <dt>Ride</dt>
                        <dd>{opt.ride_min} min</dd>
                        <dt>Walk</dt>
                        <dd>{opt.walk_min} min</dd>
                        <dt>Predicted load</dt>
                        <dd>
                          {pct(opt.crowding)}
                          {opt.crowding >= 0.75 ? ' (crowded)' : ''}
                        </dd>
                      </dl>
                      <p className="meta">
                        <Badge kind="DERIVED · GTFS" />{' '}
                        <Badge kind="PREDICTED" />
                        {(opt.source === 'mock' || opt.source === 'simulated') && (
                          <> <Badge kind="SIMULATED" /></>
                        )}
                      </p>
                    </article>
                  </li>
                ))}
              </ol>
              <p className="meta">
                Effective time = half the scheduled headway + ride + walk + 20 min × predicted load.
              </p>
            </>
          )}
        </section>
      )}
    </main>
  );
}
