import {cleanText, parseUtc, readableName} from '../format';
import type {Route} from '../format';
import {AboutThisData, Badge, EmptyState, PageHeader, RouteChipList, Status, StopName, TimeLabel, useApi} from '../ui';

type Event = {event_id: string | number; title: string; start_ts: string; end_ts?: string; venue_key?: string; venue_name?: string; significance?: number; time_tba?: boolean; affected_routes?: string[]; impacts?: {event_uplift: number}[]};
type Alert = {title: string; text: string; routes?: string[]; start_ts?: string; end_ts?: string; closed_stop_names?: string[]};
export default function Events() {
  const events = useApi<{data: Event[]; source: string}>('/api/events/impacts');
  const alerts = useApi<{data: Alert[]; source: string}>('/api/alerts');
  const routes = useApi<Route[]>('/api/routes');
  const game = (e: Event) => e.event_id === 'tennessee-2026-11-14';
  const list = [...(events.data?.data ?? [])].filter(e => game(e) || !(e.venue_key?.toLowerCase().replace(/_/g,' ') === 'kyle field' && e.start_ts.startsWith('2026-11-14')))
    .sort((a,b) => Number(game(b))-Number(game(a)) || (parseUtc(a.start_ts)?.getTime() ?? Infinity)-(parseUtc(b.start_ts)?.getTime() ?? Infinity));
  const alertList = [...new Map((alerts.data?.data ?? []).map(a => [cleanText(a.title)+'|'+cleanText(a.text),a])).values()]
    .filter(a => !a.end_ts || (parseUtc(a.end_ts)?.getTime() ?? Infinity) >= Date.now())
    .sort((a,b) => (parseUtc(a.start_ts)?.getTime() ?? 0)-(parseUtc(b.start_ts)?.getTime() ?? 0));
  return <main className="dashboard" aria-labelledby="page-title">
    <PageHeader title="Events" lede="Plan around campus events and bus detours. All times are Central time."/>
    <Status loading={events.loading || routes.loading} error={events.error || routes.error}/>
    {!events.loading && !events.error && !list.length && <EmptyState title="No major events listed">New campus events will appear here as the calendar updates.</EmptyState>}
    <div className="cards event-cards">{list.map(e => <article key={e.event_id} className={game(e) ? 'highlight game-card' : ''}>
      {game(e) && <span className="eyebrow">GAME DAY · FEATURED</span>}
      <h2>{cleanText(e.title)}</h2>
      <p className="event-when"><TimeLabel value={e.start_ts} dateOnly={e.time_tba}/>{e.time_tba && ' · Kickoff time to be announced'}</p>
      <p className="meta">{e.venue_name || readableName(e.venue_key) || 'Venue to be announced'}</p>
      <p><strong>Expected impact:</strong> {(e.significance ?? 0) >= 14 ? 'Buses near the venue may be much busier.' : (e.significance ?? 0) >= 10 ? 'Expect busier buses near the venue.' : 'Some extra riders near the venue.'} <Badge kind="SIMULATED"/></p>
      {!!e.impacts?.length && <p>Modelled increase: about {Math.round(Math.max(...e.impacts.map(p => p.event_uplift))*100)} percentage points at the busiest nearby route.</p>}
      <RouteChipList codes={e.affected_routes ?? []} routes={routes.data} label="Stops within about a 7 min walk"/>
    </article>)}</div>
    <section aria-labelledby="alerts-heading"><h2 id="alerts-heading">Service alerts</h2><p className="meta">Current and upcoming detours from Texas A&M Transportation Services.</p>
      <Status loading={alerts.loading} error={alerts.error}/>
      {!alerts.loading && !alerts.error && !alertList.length && <EmptyState title="No service alerts">Check here before your next trip for detours and stop closures.</EmptyState>}
      <div className="cards">{alertList.map((a,i) => <article key={i}>
        <h3>{cleanText(a.title)}</h3>
        <p className="meta"><TimeLabel value={a.start_ts}/>{a.end_ts ? <> – <TimeLabel value={a.end_ts}/></> : ' · Until further notice'}</p>
        <p>{/detour|disruption/i.test(a.title) ? 'Some stops or routes may change during this period. Check the notice before travelling.' : 'Check the notice for changes to your bus service.'}</p>
        {a.routes?.length ? <RouteChipList codes={a.routes} routes={routes.data} label="Affected routes"/> : <p className="meta">Affected routes were not specified.</p>}
        {!!a.closed_stop_names?.length && <p><strong>Closed stops:</strong> {a.closed_stop_names.map((name,i) => <span key={name}>{i > 0 && "; "}<StopName name={name}/></span>)}</p>}
        <details className="alert-notice"><summary>Read the service notice</summary>{cleanText(a.text).split(/(?<=\.)\s+(?=[A-Z0-9])/).map((line,i) => <p key={i}>{line}</p>)}</details>
      </article>)}</div>
    </section>
    <AboutThisData sources={{'Event source': events.data?.source, 'Alert source': alerts.data?.source}}><p>Event impact is simulated: it is an assumption about extra demand, not a measured effect. Event-specific forecast increases appear only when a forecast overlaps the event. Nearby routes have stops within 600 m; they may not all run at the event time. Duplicate game listings and identical alerts are combined.</p></AboutThisData>
  </main>;
}
