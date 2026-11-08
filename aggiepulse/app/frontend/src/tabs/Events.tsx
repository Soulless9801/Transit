import {formatDateTime, parseUtc, significanceLevel, points} from '../format';
import {Badge, PageHeader, SourceLine, Status, useApi} from '../ui';

type Event = {
  event_id: number | string;
  title: string;
  start_ts: string;
  end_ts?: string | null;
  venue_key?: string;
  lat?: number;
  lon?: number;
  significance?: number;
  affected_routes?: string[];
  impact_provenance?: string;
  time_tba?: boolean;
  route_directions?: {route_short_name: string; direction_id: string}[];
  impacts?: {route: string; direction: string; horizon_min: number; event_uplift: number; target_ts: string}[];
};

type Alert = {
  title: string;
  text: string;
  routes?: string[];
  start_ts?: string | null;
  end_ts?: string | null;
};

function EventImpact({event}: {event: Event}) {
  return <><p>Nearby routes (stops within 600 m): {event.affected_routes?.join(', ') || 'No nearby stops found'}.</p>
    {!!event.route_directions?.length && <p className="meta">Mapped route-directions: {event.route_directions.map(r => `${r.route_short_name} / ${r.direction_id}`).join(', ')}</p>}
    <Badge kind="SIMULATED PRIOR"/>
    {event.impacts?.length ? <div className="table-scroll"><table className="data-table"><caption>Model event uplift for forecasts overlapping this event; load percentage points, not a measured causal effect.</caption><thead><tr><th scope="col">Route / direction</th><th scope="col">Horizon</th><th scope="col">Target (Central)</th><th scope="col">Uplift</th></tr></thead><tbody>{event.impacts.map((p, i) => <tr key={i}><th scope="row">{p.route} / {p.direction}</th><td>+{p.horizon_min} min</td><td>{formatDateTime(parseUtc(p.target_ts)!)}</td><td>{points(p.event_uplift)}</td></tr>)}</tbody></table></div> : <p>No event-window forecast uplift is available yet. High demand remains an assumed prior; current forecasts are not projected forward to this event.</p>}
  </>;
}

export default function Events() {
  const events = useApi<{data: Event[]; source: string; route_mapping_source: string}>('/api/events/impacts');
  const alerts = useApi<{data: Alert[]; source: string}>('/api/alerts');

  const game = events.data?.data.find(e => e.event_id === 'tennessee-2026-11-14');
  const sortedEvents = [...(events.data?.data ?? [])].filter(e => e.event_id !== 'tennessee-2026-11-14').sort((a, b) => {
    const ta = parseUtc(a.start_ts)?.getTime() ?? Infinity;
    const tb = parseUtc(b.start_ts)?.getTime() ?? Infinity;
    return ta - tb;
  });

  const alertList = alerts.data?.data ?? [];

  return (
    <main className="dashboard" aria-labelledby="page-title">
      <PageHeader
        title="Events"
        lede="Campus events in the next seven days that may affect ridership, and active TripSpark service alerts."
      />

      <article className="highlight">
        <Badge kind="SIMULATED PRIOR" />
        <h2>Football vs Tennessee</h2>
        <p>Kyle Field · Saturday, Nov 14, 2026</p>
        <p>Kickoff time TBA. Observed game-day impact is not yet available.</p>
        {game && <EventImpact event={game}/>}
      </article>

      <h2>Upcoming events</h2>
      <SourceLine kinds={['DERIVED · EVENT FILTERS']} source={events.data?.source} />
      <p className="meta">Nearby routes use GTFS stops; direction mappings: {events.data?.route_mapping_source || 'loading'}. Uplift is a simulated model prior, not an isolated effect of one event.</p>
      <Status
        loading={events.loading}
        error={events.error}
        empty={!events.loading && !events.error && sortedEvents.length === 0}
        emptyText="No upcoming events."
      />
      <div className="cards">
        {sortedEvents.map(e => {
          const start = parseUtc(e.start_ts);
          const when = start ? formatDateTime(start) : 'Time TBA';
          const where = e.venue_key || 'Venue TBA';
          const routes = e.affected_routes && e.affected_routes.length > 0
            ? e.affected_routes.join(', ')
            : 'No stops within 600 m';
          const impact = significanceLevel(e.significance ?? 0);

          return (
            <article key={e.event_id}>
              <h3>{e.title}</h3>
              <dl className="facts">
                <dt>When</dt>
                <dd>{when}</dd>
                <dt>Where</dt>
                <dd>{where}</dd>
                <dt>Nearby routes</dt>
                <dd>{routes}</dd>
                <dt>Expected impact</dt>
                <dd>
                  {impact} <Badge kind="SIMULATED PRIOR" />
                </dd>
              </dl>
              <EventImpact event={e}/>
            </article>
          );
        })}
      </div>

      <h2>Service alerts</h2>
      <SourceLine kinds={['DERIVED · SERVICE ALERT']} source={alerts.data?.source} />
      <Status
        loading={alerts.loading}
        error={alerts.error}
        empty={!alerts.loading && !alerts.error && alertList.length === 0}
        emptyText="No active service alerts."
      />
      {alertList.map((a, i) => {
        const routesText = a.routes && a.routes.length > 0 ? `Routes: ${a.routes.join(', ')}` : 'All routes';
        const parsedStart = parseUtc(a.start_ts);
        const sinceText = parsedStart ? ` · Since ${formatDateTime(parsedStart)}` : '';

        return (
          <article key={i}>
            <h3>{a.title}</h3>
            <p>{a.text.trim()}</p>
            <p className="meta">
              {routesText}
              {sinceText}
            </p>
          </article>
        );
      })}
    </main>
  );
}
