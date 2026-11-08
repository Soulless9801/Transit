import {compareRoutes, directionName, factorPhrase, parseUtc, pct} from '../format';
import type {Freshness, Prediction, Route} from '../format';
import {AboutThisData, Badge, EmptyState, IntervalBar, LoadCell, LoadLegend, PageHeader, RouteChip, ServiceNotice, Status, useApi} from '../ui';
type ForecastRow = Prediction & {p_crowd: number; source?: string; model_version?: string; t0?: string};
export default function Forecast() {
  const predictions = useApi<ForecastRow[]>('/api/predictions',15000);
  const routes = useApi<Route[]>('/api/routes');
  const meta = useApi<{freshness: Freshness}>('/api/meta',15000);
  const rows = predictions.data ?? [];
  const freshness = meta.data?.freshness;
  const paused = freshness?.data_expected === false || freshness?.in_service === false;
  const current = rows.filter(p => !p.t0 || (parseUtc(p.t0)?.getTime() ?? 0) + p.horizon_min*60000 >= Date.now());
  const codes = [...new Set(current.map(p => p.route))].sort(compareRoutes);
  const top = [...current.filter(p => p.horizon_min === 30).reduce((best, p) => { const prev = best.get(p.route); if (!prev || p.p_crowd > prev.p_crowd) best.set(p.route, p); return best; }, new Map<string, ForecastRow>()).values()]
    .sort((a,b) => b.p_crowd-a.p_crowd || compareRoutes(a.route,b.route)).slice(0,5);
  const chip = (code: string) => <RouteChip code={code} route={routes.data?.find(r => r.route_short_name === code)}/>;
  const simulated = current.some(p => p.provenance === 'SIMULATED' || p.source === 'mock');
  const loading = predictions.loading || routes.loading || meta.loading;
  return <main className="dashboard" aria-labelledby="page-title">
    <PageHeader title="Forecast" lede="See how full buses may be in 15, 30 and 60 min. Crowded means at least 75% full."/>
    <Status loading={loading} error={predictions.error || routes.error || meta.error}/>
    {!loading && !predictions.error && (paused || !current.length) && <ServiceNotice freshness={freshness}/>}
    {!paused && current.length > 0 && <>
      <div className="provenance"><Badge kind={simulated ? 'SIMULATED' : 'PREDICTED'}/></div>
      <section className="section"><h2>How full in the next hour?</h2><p className="meta">Each route shows its busiest direction. A dash means no forecast is available.</p>
        <LoadLegend title="Expected fullness"/>
        <div className="table-scroll" tabIndex={0} role="region" aria-label="Route forecasts"><table className="data-table forecast-table"><thead><tr><th scope="col">Route</th>{[15,30,60].map(h => <th key={h} scope="col">In {h} min</th>)}</tr></thead><tbody>{codes.map(code => <tr key={code}><th scope="row">{chip(code)}</th>{[15,30,60].map(h => {const values=current.filter(p => p.route===code && p.horizon_min===h);return <LoadCell key={h} value={values.length ? Math.max(...values.map(p => p.pred_p50)) : null}/>;})}</tr>)}</tbody></table></div>
      </section>
      <section className="section"><h2>Highest chance of crowding in 30 min</h2>
        {top.some(p => p.top_factors?.length) && <p className="meta">↑ More riders expected · ↓ Fewer riders expected</p>}
        {!top.length && <EmptyState title="No 30 min forecast yet">Other available times are shown above. Check back after the next forecast update.</EmptyState>}
        <div className="cards">{top.map((p,i) => <article key={p.route+'-'+p.direction+'-'+i}><h3>{chip(p.route)}</h3>{directionName(p.direction_name) && <p className="meta">Toward {directionName(p.direction_name)}</p>}{!directionName(p.direction_name) && <p className="meta">Busiest direction</p>}
          <p><span className="big-stat">{p.p_crowd>0 && p.p_crowd<0.01 ? '<1%' : pct(p.p_crowd)}</span> chance of being crowded</p>
          <p>Expected to be {pct(p.pred_p50)} full.<br/>Likely range: {pct(p.pred_p10)}–{pct(p.pred_p90)}.</p>
          <IntervalBar p10={p.pred_p10} p50={p.pred_p50} p90={p.pred_p90}/>
          {!!p.top_factors?.length && <><h4>What affects this estimate?</h4><ul className="factor-list">{[...p.top_factors].sort((a,b) => Math.abs(Number(b.contribution))-Math.abs(Number(a.contribution))).slice(0,3).map((f,j) => <li key={j}>{factorPhrase(f.family,f.feature,Number(f.contribution))}</li>)}</ul></>}
        </article>)}</div>
      </section>
    </>}
    <AboutThisData sources={{'Forecast source': rows[0]?.source, 'Model versions': [...new Set(rows.map(p => p.model_version).filter(Boolean))].join(', ') || 'Unavailable', 'Training data': [...new Set(rows.map(p => p.training_data_origin).filter(Boolean))].join(', ') || 'Unavailable', 'Last scored': freshness?.last_scoring_ts}}><p>Likely range is the model’s 10th–90th percentile interval, not a guarantee. Colours show the share of bus capacity occupied. Predictions trained on simulated history are labelled Predicted; fully simulated fallback estimates are labelled Simulated. Expired forecasts are hidden.</p></AboutThisData>
  </main>;
}
