import {useEffect, useId, useState} from 'react';
import {LOAD_SCALE, loadStep, pct, formatTime, parseUtc, formatDateTime} from './format';
import type {Series, Route, StopOption, Freshness} from './format';
import {compareRoutes, routeColor, safeTextColor, friendlyDate, serviceMessage, directionName} from './format';
import type {ReactNode} from 'react';

// Share pending reads across the header, page and data disclosure.
const reads = new Map<string, {expires: number; promise: Promise<unknown>}>();
function readJson(url: string): Promise<unknown> {
  const existing = reads.get(url);
  if (existing && existing.expires > Date.now()) return existing.promise;
  const entry = {expires: Infinity, promise: fetch(url).then(response => {
    if (!response.ok) throw new Error('Information unavailable');
    return response.json();
  }) as Promise<unknown>};
  reads.set(url,entry);
  entry.promise.then(() => {entry.expires = Date.now() + (url === '/api/meta' ? 10000 : 1000);}, () => {reads.delete(url);});
  return entry.promise;
}
export function useApi<T>(url: string | null, refreshMs = 0): {data: T | undefined; error: string; loading: boolean} {
  const [state, setState] = useState<{data: T | undefined; error: string; loading: boolean}>({data: undefined, error: '', loading: Boolean(url)});
  useEffect(() => {
    if (!url) { setState({data: undefined, error: '', loading: false}); return; }
    const controller = new AbortController();
    setState({data: undefined, error: '', loading: true});
    const poll = () => readJson(url).then(value => value as T).then(data => { if (!controller.signal.aborted) setState({data, error: '', loading: false}); })
      .catch(() => { if (!controller.signal.aborted) setState({data: undefined, error: 'We couldn’t load this information. Check your connection and try again.', loading: false}); });
    void poll();
    const timer = refreshMs ? setInterval(poll,refreshMs) : undefined;
    return () => {controller.abort(); if (timer) clearInterval(timer);};
  }, [url, refreshMs]);
  return state;
}
export function PageHeader({title, lede}: {title: string; lede: string}) {
  return <><span className="eyebrow">YOUR CAMPUS BUS GUIDE</span><h1 id="page-title" tabIndex={-1}>{title}</h1><p className="lede">{lede}</p></>;
}
const explanations: Record<string, string> = {
  Measured: 'Passenger counts reported by bus counters. Counter accuracy has not been independently verified.',
  Predicted: 'A model estimate, not a guarantee. Training includes simulated history while real readings accumulate.',
  Simulated: 'An estimate based on assumed effects or simulated history, not an observed count.',
};
export function InfoTip({label, children}: {label: string; children: ReactNode}) {
  return <details className="info-tip"><summary aria-label={label}>ⓘ</summary><div role="note">{children}</div></details>;
}
export function Badge({kind}: {kind: string}) {
  const label = /SIMULATED|mock/i.test(kind) ? 'Simulated' : /PREDICTED/i.test(kind) ? 'Predicted' : /MEASURED/i.test(kind) ? 'Measured' : kind === 'REPLAY' ? 'Recorded' : kind;
  return <span className="badge-group"><span className={label === 'Simulated' ? 'badge badge--sim' : 'badge'}>{label}</span>{explanations[label] && <InfoTip label={`About ${label.toLowerCase()} data`}>{explanations[label]}</InfoTip>}</span>;
}
export function SourceLine({kinds, source}: {kinds: string[]; source?: string}) {
  return <div className="meta provenance">{[...new Set(source === 'mock' ? ['SIMULATED'] : kinds)].map(kind => <Badge key={kind} kind={kind}/>)}</div>;
}
export function AboutThisData({children, sources = {}}: {children?: ReactNode; sources?: Record<string, unknown>}) {
  const [open,setOpen] = useState(false);
  const meta = useApi<Record<string, any>>(open ? '/api/meta' : null);
  return <details className="about-data" onToggle={e => setOpen(e.currentTarget.open)}><summary>About this data</summary>{children}<p>Times are Central time. Schedules come from Texas A&M Transportation Services. Estimates may change.</p><dl>{Object.entries(sources).filter(([,v]) => v != null).map(([key,value]) => <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}</dl>{meta.data && <details><summary>Source and freshness details</summary><pre>{JSON.stringify(meta.data, null, 2)}</pre></details>}{meta.error && <p>Source details are temporarily unavailable.</p>}</details>;
}
export function EmptyState({title = 'Nothing to show yet', children}: {title?: string; children?: ReactNode}) {
  return <div className="empty-state" role="status"><strong>{title}</strong>{children && <p>{children}</p>}</div>;
}
export function ServiceNotice({freshness, noun}: {freshness?: Freshness; noun?: string}) {
  return <EmptyState title={freshness?.in_service === false ? 'Buses are off for now' : 'Waiting for fresh data'}>{serviceMessage(freshness, noun)}</EmptyState>;
}
export function Status({loading, error, empty, emptyText}: {loading?: boolean; error?: string; empty?: boolean; emptyText?: string}) {
  if (error) return <p role="alert" className="status-msg">We couldn’t load this information. Check your connection and refresh to try again.</p>;
  if (loading) return <div role="status" className="skeleton"><span className="visually-hidden">Loading information…</span><i/><i/><i/></div>;
  if (empty) return <EmptyState>{emptyText}</EmptyState>;
  return null;
}
export function RouteChip({route, code}: {route?: Route; code?: string}) {
  const routes = useApi<Route[]>(!route ? '/api/routes' : null);
  const r = route ?? routes.data?.find(r => r.route_short_name === code);
  const bg = routeColor(r?.route_color);
  return <span className="route-chip"><b style={{background: bg, color: safeTextColor(bg, r?.route_text_color)}}>{r?.route_short_name || code || 'Bus'}</b><span>{r?.route_long_name || 'Route name unavailable'}</span></span>;
}
export function RouteChipList({codes, routes = [], limit = 3, label = 'Nearby routes'}: {codes: string[]; routes?: Route[]; limit?: number; label?: string}) {
  const sorted = [...new Set(codes)].sort(compareRoutes);
  const chip = (code: string) => <RouteChip key={code} code={code} route={routes.find(r => r.route_short_name === code)}/>;
  return <div className="route-chip-list"><p className="meta">{label} · {sorted.length} {sorted.length === 1 ? 'route' : 'routes'}</p><div className="chip-wrap">{sorted.slice(0,limit).map(chip)}</div>{sorted.length > limit && <details><summary>Show all {sorted.length} routes</summary><div className="chip-wrap">{sorted.slice(limit).map(chip)}</div></details>}</div>;
}
export function StopName({name}: {name: string}) { return <span>{name || 'Stop name unavailable'}</span>; }
export function TimeLabel({value, dateOnly = false}: {value?: string | null; dateOnly?: boolean}) {
  return value ? <time dateTime={value}>{friendlyDate(value, new Date(), dateOnly)}{!dateOnly && ' CT'}</time> : <>Time to be announced</>;
}
export function StopPicker({label, value, onChange, options, error}: {label: string; value: string; onChange: (v: string) => void; options: StopOption[]; error?: string}) {
  const id = useId(); const [open,setOpen] = useState(false); const [active,setActive] = useState(0);
  const matches = options.filter(o => o.label.toLowerCase().includes(value.toLowerCase())).slice(0,30);
  useEffect(() => {if (open) document.getElementById(id+'-'+active)?.scrollIntoView({block:'nearest'});},[active,open,id]);
  const choose = (option: StopOption) => {onChange(option.label); setOpen(false);};
  return <div className="stop-picker"><label className="field" htmlFor={id}>{label}</label><input id={id} role="combobox" aria-autocomplete="list" aria-expanded={open} aria-controls={id+'-list'} aria-activedescendant={open && matches[active] ? id+'-'+active : undefined} aria-invalid={Boolean(error)} aria-describedby={error ? id+'-error' : undefined} autoComplete="off" placeholder="Search by stop name" value={value} onFocus={() => setOpen(true)} onBlur={() => setOpen(false)} onChange={e => {onChange(e.target.value); setActive(0); setOpen(true);}} onKeyDown={e => {
    if (e.key === 'Escape') setOpen(false);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {e.preventDefault(); setOpen(true); setActive(a => Math.max(0,Math.min(matches.length-1,a+(e.key === 'ArrowDown' ? 1 : -1))));}
    if (e.key === 'Enter' && open && matches[active]) {e.preventDefault(); choose(matches[active]);}
  }}/>{open && <ul role="listbox" id={id+'-list'} aria-label={`${label} stops`}>{matches.map((o,i) => <li role="option" aria-selected={i===active} id={id+'-'+i} key={o.id} onMouseDown={e => e.preventDefault()} onClick={() => choose(o)}><StopName name={o.label}/></li>)}{!matches.length && <li>No matching stops. Try a nearby street or landmark.</li>}</ul>}{error && <p className="field-error" id={id+'-error'}>{error}</p>}</div>;
}
export function LoadCell({value, detail}: {value: number | null | undefined; detail?: string}) {
  const step = loadStep(value);
  if (!step) return <td className="nodata">—<span className="visually-hidden">no data</span></td>;
  return <td className="numeric" style={{background: step.bg, color: step.fg}}>{pct(value)}{detail && <><br/><small>{detail}</small></>}</td>;
}
export function LoadLegend({title = 'Predicted load'}: {title?: string}) {
  return <div className="load-legend"><strong>{title}:</strong> {LOAD_SCALE.map(step => <span key={step.min}><span className="swatch" style={{background: step.bg}}/> {step.label}</span>)}<span>— no data</span></div>;
}
export function IntervalBar({p10, p50, p90}: {p10: number; p50: number; p90: number}) {
  const clamp = (value: number) => Math.max(0, Math.min(100, value * 100));
  return <div className="interval" role="img" aria-label={`Likely range ${pct(p10)} to ${pct(p90)}, median ${pct(p50)}`}><span style={{left: `${clamp(p10)}%`, width: `${Math.max(0, clamp(p90) - clamp(p10))}%`}}/><span className="median" style={{left: `${clamp(p50)}%`}}/></div>;
}
export function LineChart({series, ariaLabel}: {series: Series[]; ariaLabel: string}) {
  const all = series.flatMap(s => s.points);
  if (!all.length) return <Status empty emptyText="No observed windows for this route."/>;
  const min = Math.min(...all.map(p => p.t.getTime())), max = Math.max(...all.map(p => p.t.getTime()));
  const x = (time: number) => min === max ? 468 : 56 + (time - min) / (max - min) * 824;
  const y = (value: number) => 240 - Math.max(0, Math.min(1, value)) * 220;
  const style = (i: number) => i === 0 ? {color: '#500000', dash: undefined} : i === 1 ? {color: '#8e6666', dash: '8 5'} : {color: '#5f5b5d', dash: '2 4'};
  const ticks = min === max ? [min] : Array.from({length: 6}, (_, i) => min + (max - min) * i / 5);
  return <figure className="chart" tabIndex={0}><figcaption>Measured fullness over time. Scroll for all times, or open the data table below.</figcaption><svg viewBox="0 0 900 280" role="img" aria-label={ariaLabel}>
    {[0, 0.25, 0.5, 0.75, 1].map(v => <g key={v}><line x1="56" x2="880" y1={y(v)} y2={y(v)} stroke={v === 0.75 ? '#500000' : '#dfd8d3'} strokeDasharray={v === 0.75 ? '5 4' : undefined}/><text x="48" y={y(v) + 4} textAnchor="end">{v * 100}%</text></g>)}
    <text x="875" y={y(0.75) - 5} textAnchor="end">Crowded (75%)</text>
    {ticks.map((t, i) => <g key={i}><line x1={x(t)} x2={x(t)} y1="240" y2="245" stroke="#5f5b5d"/><text x={x(t)} y="260" textAnchor={ticks.length > 1 && i === 0 ? 'start' : ticks.length > 1 && i === ticks.length - 1 ? 'end' : 'middle'}>{formatTime(new Date(t))}</text></g>)}<text x="468" y="278" textAnchor="middle">Time (Central)</text>
    {series.map((s, i) => <g key={s.direction}><polyline fill="none" stroke={style(i).color} strokeWidth="3" strokeDasharray={style(i).dash} points={s.points.map(p => `${x(p.t.getTime())},${y(p.p90)}`).join(' ')}/>{s.points.map((p, j) => <circle key={j} cx={x(p.t.getTime())} cy={y(p.p90)} r="4" fill={style(i).color}/>)}</g>)}
  </svg><ul>{series.map((s, i) => <li key={s.direction}><svg width="30" height="10" viewBox="0 0 30 10" aria-hidden="true" style={{width: 30, height: 10}}><line x1="0" x2="30" y1="5" y2="5" stroke={style(i).color} strokeWidth="3" strokeDasharray={style(i).dash}/></svg> {s.label}</li>)}</ul><details className="data-fallback"><summary>Show data table</summary><table className="data-table"><thead><tr><th>Direction</th><th>Time</th><th>Busy bus load</th><th>Readings</th></tr></thead><tbody>{series.flatMap(s => s.points.map((p, i) => <tr key={`${s.direction}-${i}`}><td>{s.label}</td><td>{friendlyDate(p.t)}</td><td>{pct(p.p90)}</td><td>{p.n}</td></tr>))}</tbody></table></details></figure>;
}

export function ComparisonChart({rows}: {rows: import('./format').ComparisonRow[]}) {
  const times = rows.map(r => parseUtc(r.target_window_start)!.getTime());
  const min = Math.min(...times), max = Math.max(...times);
  const x = (t: number) => min === max ? 468 : 56 + (t - min) / (max - min) * 824;
  const y = (v: number) => 240 - Math.max(0, Math.min(1, v)) * 220;
  const directions = [...new Set(rows.map(r => r.direction_id))];
  const stamp = (r: typeof rows[number]) => parseUtc(r.target_window_start)!.getTime();
  return <figure className="chart" tabIndex={0}><svg viewBox="0 0 900 280" role="img" aria-label="Expected load and likely range compared with measured busy bus load">
    {[0, 0.25, 0.5, 0.75, 1].map(v => <g key={v}><line x1="56" x2="880" y1={y(v)} y2={y(v)} stroke="#dfd8d3"/><text x="48" y={y(v) + 4} textAnchor="end">{pct(v)}</text></g>)}
    {directions.map((direction, i) => {
      const points = rows.filter(r => r.direction_id === direction);
      return <g key={direction}>
        <polygon fill={i % 2 ? '#a96800' : '#500000'} fillOpacity="0.14" points={[...points.map(r => `${x(stamp(r))},${y(r.pred_p90)}`), ...[...points].reverse().map(r => `${x(stamp(r))},${y(r.pred_p10)}`)].join(' ')}/>
        <polyline fill="none" stroke="#500000" strokeWidth="2" strokeDasharray={i % 2 ? '2 4' : '8 5'} points={points.map(r => `${x(stamp(r))},${y(r.pred_p50)}`).join(' ')}/>
        <polyline fill="none" stroke={i % 2 ? '#a96800' : '#17623d'} strokeWidth="3" points={points.map(r => `${x(stamp(r))},${y(r.actual_p90_load!)}`).join(' ')}/>
        {points.map((r, j) => <circle key={j} r="3" fill={i % 2 ? '#a96800' : '#17623d'} cx={x(stamp(r))} cy={y(r.actual_p90_load!)}/>)}
      </g>;
    })}
    {(min === max ? [min] : [min, min + (max - min) / 2, max]).map((t, i, all) => <text key={i} x={x(t)} y="260" textAnchor={all.length > 1 && i === 0 ? 'start' : all.length > 1 && i === all.length - 1 ? 'end' : 'middle'}>{formatTime(new Date(t))}</text>)}
    <text x="468" y="278" textAnchor="middle">Target window (Central)</text>
  </svg><figcaption>Shaded: likely range. Dashed maroon: expected load. Solid green / amber: measured busy bus load. Values are also available in the table below.</figcaption>
    <details className="data-fallback"><summary>Show comparison data</summary><div className="table-scroll"><table className="data-table"><caption>Forecast and measured load in each 15-minute period</caption><thead><tr><th scope="col">Direction</th><th scope="col">Target (Central)</th><th scope="col">Low estimate</th><th scope="col">Median</th><th scope="col">High estimate</th><th scope="col">Measured load</th><th scope="col">Readings</th></tr></thead><tbody>{rows.map((r, i) => <tr key={i}><th scope="row">{directionName(r.direction_name) || 'Route service'}</th><td>{formatDateTime(new Date(stamp(r)))}</td><td>{pct(r.pred_p10)}</td><td>{pct(r.pred_p50)}</td><td>{pct(r.pred_p90)}</td><td>{pct(r.actual_p90_load)}</td><td>{r.actual_n_pings}</td></tr>)}</tbody></table></div></details>
  </figure>;
}
