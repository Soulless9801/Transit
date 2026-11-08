import {useMemo, useState} from 'react';
import {compareRoutes, comparisonRows, comparisonMetrics, historySeries, hotspotGrid, pct} from '../format';
import type {ComparisonRow, Freshness, HistoryRow, Route} from '../format';
import {AboutThisData, Badge, ComparisonChart, EmptyState, LineChart, LoadCell, LoadLegend, PageHeader, RouteChip, Status, TimeLabel, useApi} from '../ui';
export default function History() {
  const history=useApi<{data: (HistoryRow & {data_origin?: string})[]; source: string}>('/api/history');
  const comparison=useApi<{data: ComparisonRow[]; source: string}>('/api/history/comparison');
  const routeData=useApi<Route[]>('/api/routes');
  const meta=useApi<{freshness: Freshness}>('/api/meta');
  const [selected,setSelected]=useState(''), [horizon,setHorizon]=useState(30);
  const rows=useMemo(() => (history.data?.data ?? []).filter(r => (!r.data_origin || r.data_origin==='observed') && r.p90_load_factor != null && (r.n_pings ?? 0)>0),[history.data]);
  const codes=[...new Set([...rows.map(r => r.route_short_name),...(comparison.data?.data ?? []).map(r => r.route_short_name)])].sort(compareRoutes);
  const route=selected && codes.includes(selected) ? selected : codes[0] || '';
  const chart=historySeries(rows,route), grid=hotspotGrid(rows,6);
  const aligned=comparisonRows(comparison.data?.data ?? [],route,horizon), metrics=comparisonMetrics(aligned);
  const chip=(code: string) => <RouteChip code={code} route={routeData.data?.find(r => r.route_short_name===code)}/>;
  return <main className="dashboard" aria-labelledby="page-title">
    <PageHeader title="History" lede="See how full buses have been, and how forecasts compare with real passenger readings."/>
    <div className="provenance"><Badge kind="MEASURED"/></div>
    <Status loading={history.loading || routeData.loading} error={history.error || routeData.error}/>
    {!history.loading && !history.error && !rows.length && <EmptyState title="No passenger readings to chart yet">{history.data?.data.length ? 'The available history has scheduled service but no passenger counts.' : 'No passenger readings have been collected for this view yet.'} Measured charts will appear as buses report readings.{meta.data?.freshness.in_service===false && <> Next service: <TimeLabel value={meta.data.freshness.next_scheduled_ts}/>.</>}</EmptyState>}
    {!!codes.length && <label className="field route-select">Choose a route<select value={route} onChange={e => setSelected(e.target.value)}>{codes.map(code => <option key={code} value={code}>{code} · {routeData.data?.find(r => r.route_short_name===code)?.route_long_name || 'Bus route'}</option>)}</select></label>}
    {!!chart.length && <section><h2>Busy bus load · {chip(route)}</h2><p className="meta">The busy end of readings in each 15 min period. This is higher than a typical bus, so you can plan for crowding.</p><LineChart series={chart} ariaLabel="Measured busy bus load over time"/></section>}
    <section className="section"><h2>How close were the forecasts?</h2><div className="provenance"><Badge kind="PREDICTED"/></div>
      <p>Compare expected fullness with measured busy bus load.</p>
      <label className="field route-select">Time predicted ahead<select value={horizon} onChange={e => setHorizon(Number(e.target.value))}>{[15,30,60].map(h => <option key={h} value={h}>In {h} min</option>)}</select></label>
      <Status loading={comparison.loading} error={comparison.error}/>
      {!comparison.loading && !comparison.error && (metrics.n<5 ? <EmptyState title="More completed trips are needed">{metrics.n ? `${metrics.n} of 5 comparison periods are available.` : 'No matching forecast and passenger readings yet.'} Comparisons appear after at least five 15 min periods have both a forecast and real passenger counts.</EmptyState> : <><p>Typical forecast error: <strong>{Math.round((metrics.mae ?? 0)*100)} percentage points</strong>. Measured load fell within the likely range <strong>{pct(metrics.coverage)}</strong> of the time.</p><p className="meta">Based on {metrics.n} completed comparison periods. These early results may change as more readings arrive.</p><ComparisonChart rows={aligned}/></>)}
    </section>
    {!!rows.length && <section className="section"><h2>Recent busy periods</h2><LoadLegend title="Measured busy bus load"/><p className="meta">Six most recent 15 min periods. Scroll the table to compare times.</p><div className="table-scroll" role="region" aria-label="Recent measured fullness by route" tabIndex={0}><table className="data-table"><thead><tr><th scope="col">Route</th>{grid.slots.map(t => <th key={t.getTime()} scope="col"><TimeLabel value={t.toISOString()}/></th>)}</tr></thead><tbody>{grid.routes.map(code => <tr key={code}><th scope="row">{chip(code)}</th>{grid.slots.map(t => <LoadCell key={t.getTime()} value={grid.cells[code]?.[t.getTime()]?.p90} detail={grid.cells[code]?.[t.getTime()] ? `${grid.cells[code][t.getTime()].n} readings` : undefined}/>)}</tr>)}</tbody></table></div></section>}
    <AboutThisData sources={{'History source': history.data?.source,'Comparison source': comparison.data?.source,'Passenger readings': rows.reduce((sum,r) => sum+(r.n_pings ?? 0),0),'Completed comparisons': metrics.n}}><p>Busy bus load is the 90th percentile of measured passenger counts relative to capacity in each 15 min window. Empty scheduled windows are excluded. Readings are deduplicated by bus and GPS timestamp. Actuals are always observed; forecasts may use simulated training data.</p><p>Forecast error is mean absolute error in percentage points. Likely range is the 10th–90th percentile interval. Comparisons use the latest forecast per route, direction, prediction time and target; at least five completed matches are required.</p></AboutThisData>
  </main>;
}
