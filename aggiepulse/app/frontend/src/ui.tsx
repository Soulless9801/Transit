import {useEffect, useState} from 'react';
import {LOAD_SCALE, loadStep, pct, formatTime} from './format';
import type {Series} from './format';

export function useApi<T>(url: string | null): {data: T | undefined; error: string; loading: boolean} {
  const [state, setState] = useState<{data: T | undefined; error: string; loading: boolean}>({data: undefined, error: '', loading: false});
  useEffect(() => {
    if (!url) { setState({data: undefined, error: '', loading: false}); return; }
    const controller = new AbortController();
    setState({data: undefined, error: '', loading: true});
    fetch(url, {signal: controller.signal}).then(response => {
      if (!response.ok) throw new Error(`Could not load ${url} (${response.status})`);
      return response.json() as Promise<T>;
    }).then(data => { if (!controller.signal.aborted) setState({data, error: '', loading: false}); })
      .catch(error => { if (!controller.signal.aborted) setState({data: undefined, error: String(error.message || error), loading: false}); });
    return () => controller.abort();
  }, [url]);
  return state;
}
export function PageHeader({title, lede}: {title: string; lede: string}) {
  return <><span className="eyebrow">AGGIEPULSE INTELLIGENCE</span><h1 id="page-title" tabIndex={-1}>{title}</h1><p className="lede">{lede}</p></>;
}
export function Badge({kind}: {kind: string}) { return <span className={kind.includes('SIMULATED') ? 'badge badge--sim' : 'badge'}>{kind}</span>; }
export function SourceLine({kinds, source}: {kinds: string[]; source?: string}) {
  const badges = source === 'mock' && !kinds.includes('SIMULATED') ? [...kinds, 'SIMULATED'] : kinds;
  return <p className="meta">{badges.map(kind => <Badge key={kind} kind={kind}/>)}{source && <> · Source: {source}</>}</p>;
}
export function Status({loading, error, empty, emptyText}: {loading?: boolean; error?: string; empty?: boolean; emptyText?: string}) {
  if (error) return <p role="alert" className="status-msg">{error}</p>;
  if (loading) return <p role="status" className="status-msg">Loading…</p>;
  if (empty) return <p className="status-msg">{emptyText}</p>;
  return null;
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
  return <figure className="chart"><svg viewBox="0 0 900 280" role="img" aria-label={ariaLabel}>
    {[0, 0.25, 0.5, 0.75, 1].map(v => <g key={v}><line x1="56" x2="880" y1={y(v)} y2={y(v)} stroke={v === 0.75 ? '#500000' : '#dfd8d3'} strokeDasharray={v === 0.75 ? '5 4' : undefined}/><text x="48" y={y(v) + 4} textAnchor="end">{v * 100}%</text></g>)}
    <text x="875" y={y(0.75) - 5} textAnchor="end">Crowded (75%)</text>
    {ticks.map((t, i) => <g key={i}><line x1={x(t)} x2={x(t)} y1="240" y2="245" stroke="#5f5b5d"/><text x={x(t)} y="260" textAnchor="middle">{formatTime(new Date(t))}</text></g>)}<text x="468" y="278" textAnchor="middle">Time (Central)</text>
    {series.map((s, i) => <g key={s.direction}><polyline fill="none" stroke={style(i).color} strokeWidth="3" strokeDasharray={style(i).dash} points={s.points.map(p => `${x(p.t.getTime())},${y(p.p90)}`).join(' ')}/>{s.points.map((p, j) => <circle key={j} cx={x(p.t.getTime())} cy={y(p.p90)} r="4" fill={style(i).color}/>)}</g>)}
  </svg><ul>{series.map((s, i) => <li key={s.direction}><svg width="30" height="10" viewBox="0 0 30 10" aria-hidden="true" style={{width: 30, height: 10}}><line x1="0" x2="30" y1="5" y2="5" stroke={style(i).color} strokeWidth="3" strokeDasharray={style(i).dash}/></svg> {s.label}</li>)}</ul><details className="data-fallback"><summary>Show data table</summary><table className="data-table"><thead><tr><th>Direction</th><th>Time</th><th>p90 load</th><th>Pings</th></tr></thead><tbody>{series.flatMap(s => s.points.map((p, i) => <tr key={`${s.direction}-${i}`}><td>{s.label}</td><td>{formatTime(p.t)}</td><td>{pct(p.p90)}</td><td>{p.n}</td></tr>))}</tbody></table></details></figure>;
}
