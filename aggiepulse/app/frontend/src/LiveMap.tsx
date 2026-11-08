import {useEffect, useMemo, useRef, useState} from 'react';
import * as maplibregl from 'maplibre-gl';
import {Map as GLMap, Marker, Popup} from 'maplibre-gl';
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import {ageLabel, crowdColor, formatDateTime, MAP_LOAD_SCALE, parseUtc, pct, points, replayFrame, routeLoad} from './format';
import type {Prediction} from './format';
import {Badge, IntervalBar, Status, useApi} from './ui';
maplibregl.setWorkerUrl(maplibreWorkerUrl);

type Route = {route_id: string; route_short_name: string; route_long_name: string; route_color: string};
type Vehicle = {vehicle_id: string; route?: string; route_id?: string; lat: number; lon: number; onboard: number | null; capacity: number | null; load_factor: number | null; gps_age_s?: number | null; source?: string; ts_gps: string | null};
type Replay = {data: Vehicle[]; source: string; start_ts: string | null; end_ts: string | null; capped: boolean};
async function get<T,>(url: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(url, {signal}); if (!r.ok) throw new Error(`${url}: ${r.status}`); return r.json();
}
function RouteForecast({route, predictions}: {route: string; predictions: Prediction[]}) {
  const rows = predictions.filter(p => p.route === route);
  return <section className="route-forecast" aria-label={`Route ${route} forecast`}>
    <h2>Route {route} <Badge kind="PREDICTED"/></h2>
    {!rows.length && <p>No predictions for this route.</p>}
    {rows.some(p => p.provenance === 'SIMULATED') && <Badge kind="SIMULATED"/>}
    {rows.map(p => <div key={`${p.direction}-${p.horizon_min}`}>
      <h3>+{p.horizon_min} min · {p.direction_name || `Direction ${p.direction}`}</h3>
      <p>{pct(p.pred_p50)} load · p10–p90 {pct(p.pred_p10)}–{pct(p.pred_p90)}</p>
      <IntervalBar p10={p.pred_p10} p50={p.pred_p50} p90={p.pred_p90}/>
      <ul>{(p.top_factors ?? []).slice(0, 3).map((f, i) => <li key={i}>{f.family}: {f.feature} ({points(Number(f.contribution))})</li>)}</ul>
      {p.training_data_origin && <p>Trained on {p.training_data_origin} data.</p>}
    </div>)}
  </section>;
}
export default function LiveMap() {
  const container = useRef<HTMLDivElement>(null), map = useRef<GLMap | null>(null), markers = useRef<Marker[]>([]);
  const [routes, setRoutes] = useState<Route[]>([]), [selected, setSelected] = useState<string[]>([]);
  const [vehicles, setVehicles] = useState<Vehicle[]>([]), [predictions, setPredictions] = useState<Prediction[]>([]);
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
    get<Route[]>('/api/routes', controller.signal).then(r => { setRoutes(r); setSelected(r.map(x => x.route_id)); }).catch(e => { if (!controller.signal.aborted) setError(String(e)); });
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
          new Popup().setLngLat(e.lngLat).setText(`${p.stop_name} · Stop ${p.stop_code || p.stop_id} · Routes ${p.routes} · GTFS schedule`).addTo(m);
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
            new Marker({element: el}).setLngLat([e.lon, e.lat]).setPopup(new Popup().setText(e.title + ' · simulated prior')).addTo(m);
          }
        }
        if (alerts.status === 'fulfilled') {
          const affected = alerts.value.data.filter((a: any) => (!a.start_ts || parseUtc(a.start_ts)! <= new Date()) && (!a.end_ts || parseUtc(a.end_ts)! >= new Date())).flatMap((a: any) => a.routes || []);
          m.addSource('alerts', {type: 'geojson', data: {...shapes, features: shapes.features.filter((f: any) => affected.includes(f.properties.route_short_name))}});
          m.addLayer({id: 'alert-lines', type: 'line', source: 'alerts', paint: {'line-color': '#ed9b20', 'line-width': 10, 'line-dasharray': [2, 2], 'line-opacity': 0.6}}, 'routes');
        }
      } catch (e) { if (map.current === m) setMapError(String(e)); }
    });
    return () => { markers.current.forEach(x => x.remove()); m.remove(); map.current = null; };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    async function poll() {
      const results = await Promise.allSettled([get<Vehicle[]>('/api/vehicles/latest', controller.signal), get<Prediction[]>('/api/predictions', controller.signal)]);
      if (controller.signal.aborted) return;
      const [v, p] = results;
      if (v.status === 'fulfilled') { setVehicles(v.value); setError(''); } else setError(String(v.reason));
      if (p.status === 'fulfilled') { setPredictions(p.value); setForecastError(''); } else { setPredictions([]); setForecastError(String(p.reason)); }
    }
    void poll(); const timer = setInterval(poll, 15000);
    return () => { controller.abort(); clearInterval(timer); };
  }, []);
  useEffect(() => {
    const m = map.current; if (!ready || !m || !routes.length) return;
    m.setFilter('routes', ['in', ['get', 'route_id'], ['literal', selected]]);
    if (m.getLayer('alert-lines')) m.setFilter('alert-lines', ['in', ['get', 'route_id'], ['literal', selected]]);
    m.setFilter('stops', (selected.length ? ['any', ...selected.map(id => ['in', id, ['get', 'routes']])] : ['literal', false]) as maplibregl.ExpressionSpecification);
    m.setPaintProperty('routes', 'line-color', ['match', ['get', 'route_short_name'], routes[0].route_short_name, crowdColor(mode === 'replay' ? null : routeLoad(predictions, routes[0].route_short_name)), ...routes.slice(1).flatMap(r => [r.route_short_name, crowdColor(mode === 'replay' ? null : routeLoad(predictions, r.route_short_name))]), '#7b8490'] as maplibregl.ExpressionSpecification);
    markers.current.forEach(x => x.remove()); markers.current = [];
    visibleVehicles.filter(v => { const r = routes.find(r => r.route_id === v.route_id || r.route_short_name === v.route); return !r || selected.includes(r.route_id); }).forEach(v => {
      const el = document.createElement('button'); el.className = 'bus'; el.style.borderColor = crowdColor(v.load_factor);
      el.textContent = v.route || '▣'; el.style.background = '#500000'; el.style.color = 'white';
      el.setAttribute('aria-label', `${mode === 'replay' ? 'Replay' : 'Measured'} bus ${v.vehicle_id}, route ${v.route}, load ${pct(v.load_factor)}`);
      const body = document.createElement('div');
      const title = document.createElement('strong'); title.textContent = `${mode === 'replay' ? 'REPLAY · ' : ''}Bus ${v.vehicle_id} · Route ${v.route || 'unknown'}`; body.append(title);
      for (const text of [`${v.onboard ?? '—'} / ${v.capacity ?? '—'} onboard · ${pct(v.load_factor)} · MEASURED · APC`, `GPS: ${parseUtc(v.ts_gps) ? formatDateTime(parseUtc(v.ts_gps)!) + ' Central' : 'unknown'}`, mode === 'replay' ? `Recorded source: ${replay.data?.source}` : `GPS age: ${ageLabel(v.gps_age_s)} · Source: ${v.source || 'unknown'}`]) {
        const p = document.createElement('p'); p.textContent = text; body.append(p);
      }
      markers.current.push(new Marker({element: el}).setLngLat([v.lon, v.lat]).setPopup(new Popup({offset: 20}).setDOMContent(body)).addTo(m));
    });
  }, [ready, selected, visibleVehicles, routes, predictions, mode, replay.data]);
  return <main className="live" aria-labelledby="page-title"><aside>
    <span className="eyebrow">CAMPUS IN MOTION</span><h1 id="page-title" tabIndex={-1}>{mode === 'replay' ? 'Replay map' : 'Live map'}</h1>
    <p className="intro">Route lines predict +30 min load; bus rings show measured load.</p>
    <label className="field">Map mode<select value={mode} onChange={e => setMode(e.target.value as 'live' | 'replay')}><option value="live">Live</option><option value="replay">Replay collected data</option></select></label>
    {mode === 'replay' && <section className="replay-controls" aria-label="Replay controls">
      <Badge kind="REPLAY"/><p>Recorded observed positions. Route forecasts are hidden during replay.</p>
      <label className="field">Collected window<select value={hours} onChange={e => setHours(Number(e.target.value))}>{[1, 6, 24, 72, 168].map(h => <option key={h} value={h}>Last {h} hours collected</option>)}</select></label>
      <Status loading={replay.loading} error={replay.error} empty={!replay.loading && !replay.error && !replay.data?.data.length} emptyText="No observed positions available in this archive."/>
      {start > 0 && <><label className="field">Replay time (Central)<input aria-label="Replay time" type="range" min={start} max={end} step={1000} value={replayTime} onChange={e => {setCursor(Number(e.target.value)); setPlaying(false);}}/></label>
        <output>{formatDateTime(new Date(replayTime))} CT</output>
        <p className="meta">{formatDateTime(new Date(start))}–{formatDateTime(new Date(end))} CT · {replay.data?.source}{replay.data?.capped ? ' · 10,000-row cap reached' : ''}</p>
        <button className="btn" aria-pressed={playing} disabled={start === end} onClick={() => {if (replayTime >= end) setCursor(start); setPlaying(p => !p);}}>{playing ? 'Pause' : 'Play'}</button>
        <label className="field">Speed<select value={speed} onChange={e => setSpeed(Number(e.target.value))}>{[1, 15, 60, 300].map(s => <option key={s} value={s}>{s}×</option>)}</select></label>
        <p>Positions expire after 2 minutes without a ping; gaps remain empty.</p></>}
    </section>}
    {mode === 'live' && <Status error={error || forecastError}/>}
    {routeDetail && mode === 'live' && <><button onClick={() => setRouteDetail('')}>Close route forecast</button><RouteForecast route={routeDetail} predictions={predictions}/></>}
    <h2>Routes <span className="gtfs">GTFS schedule</span></h2><div className="actions"><button onClick={() => setSelected(routes.map(r => r.route_id))}>Show all</button><button onClick={() => setSelected([])}>Hide all</button></div>
    <div className="route-list">{routes.map(r => <div className="route-row" key={r.route_id}><label><input type="checkbox" checked={selected.includes(r.route_id)} onChange={() => setSelected(s => s.includes(r.route_id) ? s.filter(x => x !== r.route_id) : [...s, r.route_id])}/><span className="swatch" style={{background: crowdColor(mode === 'replay' ? null : routeLoad(predictions, r.route_short_name))}}/><strong>{r.route_short_name}</strong><span>{r.route_long_name}</span></label>{mode === 'live' && <button aria-label={`Forecast for route ${r.route_short_name}`} onClick={() => setRouteDetail(r.route_short_name)}>Forecast</button>}</div>)}</div>
    <footer>Schedule © TAMU Transportation Services. APC readings reported by TripSpark.</footer>
  </aside><section className="map-wrap" aria-label={mode === 'replay' ? 'Replay campus bus map' : 'Live campus bus map'}><div ref={container} className="map"/>
    {mapError && <div className="error" role="status">{mapError}</div>}
    <div className="legend">{mode === 'replay' ? <><Badge kind="REPLAY"/> Grey routes: GTFS geometry</> : <><Badge kind="PREDICTED"/> Lines: +30 min load, busiest direction{predictions.some(p => p.provenance === 'SIMULATED') && <Badge kind="SIMULATED"/>}</>}<br/><Badge kind="MEASURED · APC"/> Circular bus rings: onboard load
      <div>{MAP_LOAD_SCALE.map(s => <span key={s.color}><b style={{color: s.color}}>●</b> {s.label}</span>)}</div>
    </div>
    {!visibleVehicles.length && <div className="empty">{mode === 'replay' ? 'No buses at this replay time. Move the slider to a collected window.' : 'No buses in the latest snapshot. See header for data freshness.'}</div>}
  </section></main>;
}
