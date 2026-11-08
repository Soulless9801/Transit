import {useEffect, useMemo, useState} from 'react';
import {campusHour, parseUtc, pct, resolveStop, shiftLabel, stopOptions} from '../format';
import type {Freshness, Route} from '../format';
import {AboutThisData, Badge, EmptyState, PageHeader, RouteChip, ServiceNotice, Status, StopName, StopPicker, TimeLabel, useApi} from '../ui';
type Stops = {features: {geometry?: {coordinates: number[]}; properties: {stop_id: string; stop_name: string; routes?: string[]}}[]};
type Option = {route: string; direction: string; shift: number; wait_min: number; ride_min: number; walk_min: number; crowding: number; score: number; source: string; origin_stop_name?: string; destination_stop_name?: string; crowding_provenance?: string; crowding_scored_at?: string};
export default function PlanTrip() {
  const stops = useApi<Stops>('/api/stops');
  const routes = useApi<Route[]>('/api/routes');
  const meta = useApi<{freshness: Freshness}>('/api/meta', 15000);
  const options = useMemo(() => stopOptions(stops.data?.features ?? []),[stops.data]);
  const [from,setFrom] = useState(''), [to,setTo] = useState(''), [hour,setHour] = useState(campusHour);
  const [hourTouched, setHourTouched] = useState(false);
  const [nextServiceDefault, setNextServiceDefault] = useState(false);
  const [fromError,setFromError] = useState(''), [toError,setToError] = useState('');
  const [loading,setLoading] = useState(false), [error,setError] = useState(''), [searched,setSearched] = useState(false);
  const [results,setResults] = useState<Option[]>([]);
  useEffect(() => {
    if (hourTouched) return;
    if (meta.data?.freshness.in_service === false) {
      const nextDate = parseUtc(meta.data.freshness.next_scheduled_ts);
      if (nextDate) {
        setHour(campusHour(nextDate));
        setNextServiceDefault(true);
      }
    }
  }, [meta.data, hourTouched]);
  async function submit(e: React.FormEvent) {
    e.preventDefault(); const origin=resolveStop(options,from), destination=resolveStop(options,to);
    setFromError(origin ? '' : 'Choose a stop name from the suggestions.');
    setToError(!destination ? 'Choose a stop name from the suggestions.' : origin?.id===destination.id ? 'Choose a different destination.' : '');
    if (!origin || !destination || origin.id===destination.id) return;
    setLoading(true); setSearched(true); setResults([]); setError('');
    try {const response=await fetch(`/api/plan?origin=${encodeURIComponent(origin.id)}&destination=${encodeURIComponent(destination.id)}&hour=${hour}`); if (!response.ok) throw Error(); setResults(await response.json());}
    catch {setError('Trip options are temporarily unavailable. Please try again.');}
    finally {setLoading(false);}
  }
  const paused = meta.data?.freshness.in_service === false;
  return <main className="dashboard" aria-labelledby="page-title">
    <PageHeader title="Plan my trip" lede="Search for two stop names to compare direct bus routes and nearby stops within about a 5 min walk."/>
    {paused && <><ServiceNotice freshness={meta.data?.freshness} noun="Buses"/><p className="meta">You can still explore typical trip options for a future trip. These are estimates, not departures for a particular date.</p></>}
    <Status loading={stops.loading} error={stops.error || routes.error}/>
    <form onSubmit={submit} noValidate className="trip-form">
      <StopPicker label="From" value={from} onChange={v => {setFrom(v);setFromError('');setSearched(false);}} options={options} error={fromError}/>
      <button type="button" className="btn btn--ghost swap-btn" onClick={() => {setFrom(to);setTo(from);setFromError('');setToError('');setSearched(false);}} aria-label="Swap origin and destination">⇅ Swap stops</button>
      <StopPicker label="To" value={to} onChange={v => {setTo(v);setToError('');setSearched(false);}} options={options} error={toError}/>
      <label className="field">Typical departure hour (Central)<select value={hour} onChange={e => {setHour(Number(e.target.value));setHourTouched(true);setSearched(false);}}>{Array.from({length:24},(_,h) => <option key={h} value={h}>{h%12 || 12}:00 {h<12 ? 'AM' : 'PM'}</option>)}</select></label>
      {nextServiceDefault && !hourTouched && <p className="meta">Buses are off right now, so this starts at the next service hour.</p>}
      <button className="btn" disabled={loading || stops.loading || !options.length}>{loading ? 'Finding routes…' : 'Find trip options'}</button>
    </form>
    <Status loading={loading} error={error}/>
    {searched && !loading && !error && <section aria-live="polite"><h2>Your trip options</h2>
      {!results.length ? <EmptyState title="No direct bus route found">Try a nearby stop. This planner checks direct routes and stops within 400 m; it does not yet plan transfers.</EmptyState> : <>
        <p className="meta">Best matches first, balancing wait, ride, walking and crowding. Times are typical estimates.</p>
        <ol className="cards trip-results">{results.slice(0,6).map((o,i) => <li key={i}><article>
          {i===0 && <span className="eyebrow">BEST MATCH</span>}<h3><RouteChip code={o.route} route={routes.data?.find(r => r.route_short_name===o.route)}/></h3>
          <p>{o.shift === 0 ? 'At your selected hour' : shiftLabel(o.shift)}</p>
          <p><span className="big-stat">{Math.round(o.wait_min+o.ride_min+o.walk_min)} min</span> estimated travel time</p>
          <dl className="facts"><dt>Wait</dt><dd>About {Math.round(o.wait_min)} min</dd><dt>Ride</dt><dd>{Math.round(o.ride_min)} min</dd><dt>Walk</dt><dd>{Math.round(o.walk_min)} min</dd><dt>Expected fullness</dt><dd>{pct(o.crowding)} <Badge kind={o.crowding_provenance || (['mock','simulated'].includes(o.source) ? 'SIMULATED' : 'PREDICTED')}/></dd></dl>
          {o.origin_stop_name && <p className="meta">Board at <StopName name={o.origin_stop_name}/><br/>Get off at <StopName name={o.destination_stop_name || ''}/></p>}
          {o.crowding_scored_at && <p className="meta">Crowding estimate updated <TimeLabel value={o.crowding_scored_at}/>.</p>}
          <p className="meta">{o.crowding>=0.75 ? 'May be crowded. Allow extra time.' : 'Expected to have room.'}</p>
        </article></li>)}</ol>
      </>}
    </section>}
    <AboutThisData sources={{'Stops': options.length, 'Trip options': searched ? results.length : 'Choose stops to compare'}}><p>Stop names and typical wait/ride times come from GTFS schedules, not live departure predictions. Same-name stops show their served routes and, where needed, the north or south side. Actual service depends on the date. A short walk may lead to a different boarding stop, shown on the result.</p><p>Ranking uses half the typical headway + ride + walk + 20 × estimated fullness. When a crowding forecast is missing, the planner assumes 40% full and labels that estimate Simulated.</p></AboutThisData>
  </main>;
}
