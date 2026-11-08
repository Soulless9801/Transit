import {useEffect, useMemo, useRef, useState} from 'react';
import * as maplibregl from 'maplibre-gl';
import {Map as GLMap, Marker, Popup} from 'maplibre-gl';
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import {spreadOffsets, ageLabel, compareRoutes, directionName, factorPhrase, routeColor, safeTextColor, crowdColor, formatDateTime, MAP_LOAD_SCALE, parseUtc, pct, replayFrame, routeLoad} from './format';
import type {Freshness, Prediction} from './format';
import {AboutThisData, Badge, IntervalBar, RouteChip, ServiceNotice, Status, useApi} from './ui';
maplibregl.setWorkerUrl(maplibreWorkerUrl);

type Route = {route_id: string; route_short_name: string; route_long_name: string; route_color: string; route_text_color?: string};
type Vehicle = {vehicle_id: string; route?: string; route_id?: string; lat: number; lon: number; onboard: number | null; capacity: number | null; load_factor: number | null; gps_age_s?: number | null; source?: string; ts_gps: string | null};
type Replay = {data: Vehicle[]; source: string; start_ts: string | null; end_ts: string | null; capped: boolean};
async function get<T,>(url: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(url, {signal}); if (!r.ok) throw new Error(`${url}: ${r.status}`); return r.json();
}
function RouteForecast({route, routes, predictions}: {route: string; routes: Route[]; predictions: Prediction[]}) {
  const rows = predictions.filter(p => p.route === route);
  return <section className="route-forecast" aria-label={`Route ${route} forecast`}>
    <h2><RouteChip code={route} route={routes.find(r => r.route_short_name === route)}/></h2><Badge kind={rows.some(p => p.provenance === 'SIMULATED') ? 'SIMULATED' : 'PREDICTED'}/>
    {!rows.length && <p>No predictions for this route.</p>}
    {rows.map(p => <div key={`${p.direction}-${p.horizon_min}`}>
      <h3>In {p.horizon_min} min{directionName(p.direction_name) && ` · Toward ${directionName(p.direction_name)}`}</h3>
      <p>{pct(p.pred_p50)} full · Likely range {pct(p.pred_p10)}–{pct(p.pred_p90)}</p>
      <IntervalBar p10={p.pred_p10} p50={p.pred_p50} p90={p.pred_p90}/>
      <ul>{(p.top_factors ?? []).slice(0, 3).map((f, i) => <li key={i}>{factorPhrase(f.family, f.feature, Number(f.contribution))}</li>)}</ul>
    </div>)}
  </section>;
}
function fillBusPopup(body: HTMLDivElement, v: Vehicle, route: Route | undefined, recorded: boolean, replayTime: number): void {
  body.replaceChildren();
  const chip = document.createElement('span'); chip.className = 'route-chip';
  const code = document.createElement('b'); code.textContent = route?.route_short_name || v.route || 'Bus';
  code.style.background = routeColor(route?.route_color); code.style.color = safeTextColor(routeColor(route?.route_color), route?.route_text_color);
  const name = document.createElement('span'); name.textContent = route?.route_long_name || 'Route name unavailable';
  chip.append(code,name); body.append(chip);
  const stamp = parseUtc(v.ts_gps);
  const elapsed = recorded && stamp ? (replayTime-stamp.getTime())/1000 : v.gps_age_s;
  const occupancy = v.onboard != null && v.capacity != null ? `${Math.round(v.onboard)} of ${Math.round(v.capacity)} places taken · ${pct(v.load_factor)} full` : v.load_factor != null ? `${pct(v.load_factor)} full · Passenger count unavailable` : 'Passenger count unavailable';
  for (const text of [occupancy, `${recorded ? 'Recorded' : 'Updated'} ${ageLabel(elapsed)} ago${recorded && stamp ? ' · ' + formatDateTime(stamp) + ' CT' : ''}`]) {
    const p = document.createElement('p'); p.textContent = text; body.append(p);
  }
  const badge = document.createElement('span'); badge.className = 'badge'; badge.textContent = 'Measured'; badge.title = 'Passenger counts reported by bus counters; counter accuracy has not been independently verified.'; body.append(badge);
}
export default function LiveMap() {
  const container = useRef<HTMLDivElement>(null), map = useRef<GLMap | null>(null), markers = useRef(new Map<string, {marker: Marker; el: HTMLButtonElement; body: HTMLDivElement}>());
  const meta = useApi<{freshness: Freshness}>('/api/meta', 15000);
  const [search, setSearch] = useState('');
  const [routeBrowserOpen, setRouteBrowserOpen] = useState(() => window.innerWidth > 800);
  const [busLoading, setBusLoading] = useState(true);
  const [routes, setRoutes] = useState<Route[]>([]), [selected, setSelected] = useState<string[]>([]);
  const [vehicles, setVehicles] = useState<Vehicle[]>([]), [predictions, setPredictions] = useState<Prediction[]>([]);
  const currentPredictions = predictions.filter(p => !(p as Prediction & {t0?: string}).t0 || (parseUtc((p as Prediction & {t0?: string}).t0)?.getTime() ?? 0) + p.horizon_min * 60000 >= Date.now());
  const [error, setError] = useState(''), [forecastError, setForecastError] = useState(''), [mapError, setMapError] = useState('');
  const [ready, setReady] = useState(false), [routeDetail, setRouteDetail] = useState('');
  const [mode, setMode] = useState<'live' | 'replay'>('live'), [hours, setHours] = useState(24);
  const [cursor, setCursor] = useState(0), [playing, setPlaying] = useState(false), [speed, setSpeed] = useState(60);
  const replay = useApi<Replay>(mode === 'replay' ? `/api/replay?hours=${hours}` : null);
  const start = parseUtc(replay.data?.start_ts)?.getTime() ?? 0, end = parseUtc(replay.data?.end_ts)?.getTime() ?? 0;
  const replayTime = Math.max(start, Math.min(end, cursor || start));
  useEffect(() => { setCursor(start); setPlaying(false); }, [start, end, hours, mode]);
  useEffect(() => {
    if (!playing || !start || !end) return;
    const timer = setInterval(() => setCursor(t => Math.min(end, (t || start) + speed * 1000)), 1000);
    return () => clearInterval(timer);
  }, [playing, speed, start, end]);
  useEffect(() => { if (cursor >= end) setPlaying(false); }, [cursor, end]);
  const visibleVehicles = useMemo(() => mode === 'replay' ? replayFrame(replay.data?.data ?? [], replayTime) : vehicles, [mode, replay.data, replayTime, vehicles]);
  useEffect(() => {
    const controller = new AbortController();
    get<Route[]>('/api/routes', controller.signal).then(r => { setRoutes([...r].sort((a,b) => compareRoutes(a.route_short_name,b.route_short_name))); setSelected(r.map(x => x.route_id)); }).catch(e => { if (!controller.signal.aborted) setError('Bus information is temporarily unavailable.'); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!container.current) return;
    const m = new GLMap({container: container.current, style: 'https://tiles.openfreemap.org/styles/positron', center: [-96.340, 30.615], zoom: 14});
    map.current = m; m.addControl(new maplibregl.NavigationControl(), 'top-right');
    m.on('error', () => setMapError('Some map resources could not load. Check your connection.'));
    m.on('load', async () => {
      try {
        const [shapes, stops] = await Promise.all([get<any>('/static/routes.geojson'), get<any>('/static/stops.geojson')]);
        if (map.current !== m) return;
        m.addSource('routes', {type: 'geojson', data: shapes});
        m.addLayer({id: 'routes', type: 'line', source: 'routes', paint: {'line-color': '#7b8490', 'line-width': 5, 'line-opacity': 0.85}});
        m.addSource('stops', {type: 'geojson', data: stops});
        m.addLayer({id: 'stops', type: 'circle', source: 'stops', paint: {'circle-radius': 4, 'circle-color': '#fff', 'circle-stroke-color': '#500000', 'circle-stroke-width': 1.5}});
        m.on('click', 'routes', e => { const route = e.features?.[0]?.properties.route_short_name; if (route) setRouteDetail(route); });
        m.on('click', 'stops', e => {
          const p = e.features?.[0]?.properties; if (!p) return;
          new Popup().setLngLat(e.lngLat).setText(p.stop_name).addTo(m);
        });
        for (const layer of ['routes', 'stops']) {
          m.on('mouseenter', layer, () => { m.getCanvas().style.cursor = 'pointer'; });
          m.on('mouseleave', layer, () => { m.getCanvas().style.cursor = ''; });
        }
        setReady(true);
        const [events, alerts] = await Promise.allSettled([get<any>('/api/events'), get<any>('/api/alerts')]);
        if (map.current !== m) return;
        if (events.status === 'fulfilled') for (const e of events.value.data) {
          if (e.lat && e.lon) {
            const el = document.createElement('button'); el.className = 'venue-pulse'; el.setAttribute('aria-label', e.title);
            el.style.width = el.style.height = Math.min(35, 12 + Number(e.significance || 0)) + 'px';
            new Marker({element: el}).setLngLat([e.lon, e.lat]).setPopup(new Popup().setText(e.title + ' · Extra riders expected (simulated estimate)')).addTo(m);
          }
        }
        if (alerts.status === 'fulfilled') {
          const affected = alerts.value.data.filter((a: any) => (!a.start_ts || parseUtc(a.start_ts)! <= new Date()) && (!a.end_ts || parseUtc(a.end_ts)! >= new Date())).flatMap((a: any) => a.routes || []);
          m.addSource('alerts', {type: 'geojson', data: {...shapes, features: shapes.features.filter((f: any) => affected.includes(f.properties.route_short_name))}});
          m.addLayer({id: 'alert-lines', type: 'line', source: 'alerts', paint: {'line-color': '#ed9b20', 'line-width': 10, 'line-dasharray': [2, 2], 'line-opacity': 0.6}}, 'routes');
        }
      } catch (e) { if (map.current === m) setMapError('The map could not load. Please refresh or use the route list.'); }
    });
    return () => { markers.current.forEach(x => x.marker.remove()); markers.current.clear(); m.remove(); map.current = null; };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    async function poll() {
      const results = await Promise.allSettled([get<Vehicle[]>('/api/vehicles/latest', controller.signal), get<Prediction[]>('/api/predictions', controller.signal)]);
      if (controller.signal.aborted) return;
      const [v, p] = results;
      if (v.status === 'fulfilled') { setVehicles(v.value); setError(''); } else setError('Bus updates are temporarily unavailable.');
      setBusLoading(false);
      if (p.status === 'fulfilled') { setPredictions(p.value); setForecastError(''); } else { setPredictions([]); setForecastError('Route forecasts are temporarily unavailable.'); }
    }
    void poll(); const timer = setInterval(poll, 15000);
    return () => { controller.abort(); clearInterval(timer); };
  }, []);
  useEffect(() => {
    const m = map.current; if (!ready || !m || !routes.length) return;
    m.setFilter('routes', ['in', ['get', 'route_id'], ['literal', selected]]);
    if (m.getLayer('alert-lines')) m.setFilter('alert-lines', ['in', ['get', 'route_id'], ['literal', selected]]);
    m.setFilter('stops', (selected.length ? ['any', ...selected.map(id => ['in', id, ['get', 'routes']])] : ['literal', false]) as maplibregl.ExpressionSpecification);
    m.setPaintProperty('routes', 'line-color', ['match', ['get', 'route_short_name'], routes[0].route_short_name, crowdColor(mode === 'replay' || meta.data?.freshness.data_expected === false ? null : routeLoad(currentPredictions, routes[0].route_short_name)), ...routes.slice(1).flatMap(r => [r.route_short_name, crowdColor(mode === 'replay' || meta.data?.freshness.data_expected === false ? null : routeLoad(currentPredictions, r.route_short_name))]), '#7b8490'] as maplibregl.ExpressionSpecification);
  }, [ready, selected, routes, predictions, mode, meta.data]);
  useEffect(() => {
    const m = map.current; if (!ready || !m || !routes.length) return;
    const visible = visibleVehicles.filter(v => { const r = routes.find(r => r.route_id === v.route_id || r.route_short_name === v.route); return !r || selected.includes(r.route_id); });
    const visibleRef = {current: visible};
    const applyOffsets = () => {
      const offsets = spreadOffsets(visibleRef.current.map(v => { const p = m.project([v.lon, v.lat]); return {id: v.vehicle_id, x: p.x, y: p.y}; }));
      visibleRef.current.forEach(v => markers.current.get(v.vehicle_id)?.marker.setOffset(offsets.get(v.vehicle_id) ?? [0, 0]));
    };
    const ids = new Set(visible.map(v => v.vehicle_id));
    visible.forEach(v => {
      let entry = markers.current.get(v.vehicle_id);
      if (!entry) {
        const el = document.createElement('button'); el.dataset.vehicleId = v.vehicle_id; el.className = 'bus';
        el.style.background = '#500000'; el.style.color = 'white';
        const body = document.createElement('div');
        const marker = new Marker({element: el}).setLngLat([v.lon, v.lat]).setPopup(new Popup({offset: 20}).setDOMContent(body)).addTo(m);
        entry = {marker, el, body}; markers.current.set(v.vehicle_id, entry);
      }
      const {marker, el, body} = entry;
      marker.setLngLat([v.lon, v.lat]);
      el.style.borderColor = crowdColor(v.load_factor); el.textContent = v.route || '▣';
      const route = routes.find(r => r.route_short_name === v.route || r.route_id === v.route_id);
      el.setAttribute('aria-label', `${mode === 'replay' ? 'Recorded' : 'Measured'} bus on ${route?.route_short_name || 'an unknown route'} ${route?.route_long_name || ''}, ${pct(v.load_factor)} full`);
      fillBusPopup(body, v, route, mode === 'replay', replayTime);
    });
    markers.current.forEach((entry, id) => { if (!ids.has(id)) { entry.marker.remove(); markers.current.delete(id); } });
    applyOffsets();
    m.on('zoom', applyOffsets);
    return () => { m.off('zoom', applyOffsets); };
  }, [ready, selected, visibleVehicles, routes, mode, replayTime]);
  return <main className="live" aria-labelledby="page-title"><div className="live-panel">
    <div className="live-head">
      <span className="eyebrow">CAMPUS IN MOTION</span><h1 id="page-title" tabIndex={-1}>{mode === 'replay' ? 'Replay map' : 'Live map'}</h1>
      <p className="intro">Choose a route or bus to see details. Coloured bus rings show how full each bus is.</p>
      <label className="field">Map mode<select value={mode} onChange={e => setMode(e.target.value as 'live' | 'replay')}><option value="live">Live</option><option value="replay">Replay past bus trips</option></select></label>
      {mode === 'replay' && <section className="replay-controls" aria-label="Replay controls">
        <Badge kind="MEASURED"/><p>Watch recorded bus positions. The map shows what was reported at the selected time.</p>
        <label className="field">How far back?<select value={hours} onChange={e => setHours(Number(e.target.value))}>{[1, 6, 24, 72, 168].map(h => <option key={h} value={h}>Last {h} hours</option>)}</select></label>
        <Status loading={replay.loading} error={replay.error} empty={!replay.loading && !replay.error && !replay.data?.data.length} emptyText="No observed positions available in this archive."/>
        {start > 0 && <><label className="field">Replay time (Central)<input aria-label="Replay time" type="range" min={start} max={end} step={1000} value={replayTime} onChange={e => {setCursor(Number(e.target.value)); setPlaying(false);}}/></label>
          <output>{formatDateTime(new Date(replayTime))} CT</output>
          <p className="meta">{formatDateTime(new Date(start))}–{formatDateTime(new Date(end))} CT{replay.data?.capped ? ' · Showing the most recent available positions' : ''}</p>
          <button className="btn" aria-pressed={playing} disabled={start === end} onClick={() => {if (replayTime >= end) setCursor(start); setPlaying(p => !p);}}>{playing ? 'Pause' : 'Play'}</button>
          <label className="field">Playback speed<select value={speed} onChange={e => setSpeed(Number(e.target.value))}>{[1, 15, 60, 300].map(s => <option key={s} value={s}>{s}× · {s === 1 ? 'Real time' : `${s} seconds per second`}</option>)}</select></label>
          <p>Buses disappear if no position was recorded for 2 min. Try moving the time slider if the map is empty.</p></>}
      </section>}
      {mode === 'live' && <Status error={error || forecastError}/>}
      {routeDetail && mode === 'live' && meta.data?.freshness.data_expected !== false && <><button className="btn btn--ghost route-close" onClick={() => setRouteDetail('')}>Close route forecast</button><RouteForecast route={routeDetail} routes={routes} predictions={currentPredictions}/></>}
    </div>
    <div className="live-side">
      <details className="route-browser" open={routeBrowserOpen} onToggle={e => setRouteBrowserOpen(e.currentTarget.open)}><summary>Choose routes · {routes.length} routes</summary><h2>Find a route</h2><label className="field">Search routes<input type="search" placeholder="Route number or name" value={search} onChange={e => setSearch(e.target.value)}/></label><div className="actions"><button onClick={() => setSelected(routes.map(r => r.route_id))}>Show all</button><button onClick={() => setSelected([])}>Hide all</button></div>
      <div className="route-list">{routes.filter(r => `${r.route_short_name} ${r.route_long_name}`.toLowerCase().includes(search.toLowerCase())).map(r => <div className="route-row" key={r.route_id}><label><input type="checkbox" checked={selected.includes(r.route_id)} onChange={() => setSelected(s => s.includes(r.route_id) ? s.filter(x => x !== r.route_id) : [...s, r.route_id])}/><RouteChip route={r}/></label>{mode === 'live' && meta.data?.freshness.data_expected !== false && <button aria-label={`Forecast for route ${r.route_short_name} ${r.route_long_name}`} onClick={() => setRouteDetail(r.route_short_name)}>See forecast</button>}</div>)}</div>
      {search && !routes.some(r => `${r.route_short_name} ${r.route_long_name}`.toLowerCase().includes(search.toLowerCase())) && <p>No routes match. Try a name such as Bonfire.</p>}
      </details>
      <AboutThisData sources={{'Bus source': vehicles[0]?.source, 'Replay source': replay.data?.source, 'Replay positions': replay.data?.data.length, 'Model versions': [...new Set(predictions.map(p => (p as Prediction & {model_version?: string}).model_version).filter(Boolean))].join(', ') || 'Unavailable', 'Training data': [...new Set(predictions.map(p => p.training_data_origin).filter(Boolean))].join(', ') || 'Unavailable'}}><p>Measured fullness comes from bus passenger counters. Capacity includes all available passenger places, which can include standing room. Route lines show the busiest direction’s forecast for 30 min ahead; grey means no current forecast. Forecast training may include simulated history. Replay uses recorded GPS positions, up to 10,000 positions, and hides current forecasts. Event markers indicate assumed extra demand. Dashed amber route highlights indicate an alert, not the detour path.</p></AboutThisData>
      <footer>Schedule © TAMU Transportation Services. APC readings reported by TripSpark.</footer>
    </div>
  </div><section className="map-wrap" aria-label={mode === 'replay' ? 'Replay campus bus map' : 'Live campus bus map'}><div ref={container} className="map"/>

    <div className="map-info">
      {mapError && <p role="status">{mapError}</p>}
      {mode === 'live' && visibleVehicles.some(v => (v.gps_age_s ?? 0)>600) && <p role="status">Showing older recorded bus positions. Live updates are unavailable; recording times appear when you select a bus.</p>}
      {mode === 'live' && busLoading && <Status loading/>}
      {!visibleVehicles.length && !busLoading && (mode === 'replay' ? <p role="status">No buses at this replay time. Move the time slider to another recorded period.</p> : <ServiceNotice freshness={meta.data?.freshness} noun="Buses"/>)}
      <div className="legend">{mode === 'replay' ? <p>Grey lines: scheduled routes · Buses: recorded fullness</p> : <p><Badge kind={currentPredictions.some(p => p.provenance === 'SIMULATED') ? 'SIMULATED' : 'PREDICTED'}/> Route lines: {meta.data?.freshness.data_expected === false ? 'no current forecast' : 'expected fullness in 30 min'}<br/><Badge kind="MEASURED"/> Bus rings: reported fullness</p>}
        <div>{MAP_LOAD_SCALE.map(s => <span key={s.color}><b style={{color: s.color}}>●</b> {s.label}</span>)}</div><p className="meta">Amber dashed routes: service alert · Gold circles: events</p>
      </div>
    </div>
  </section></main>;
}
