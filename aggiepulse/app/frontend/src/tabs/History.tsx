import {cloneElement, useMemo, useState} from 'react';
import {
  compareRoutes,
  formatDateTime,
  formatTime,
  historySeries,
  hotspotGrid,
  parseUtc,
} from '../format';
import type {HistoryRow} from '../format';
import {
  LineChart,
  LoadCell,
  LoadLegend,
  PageHeader,
  SourceLine,
  Status,
  useApi,
} from '../ui';

type ApiHistoryRow = HistoryRow & {
  data_origin?: string;
  source?: string;
};

export default function History() {
  const history = useApi<{data: ApiHistoryRow[]; source: string}>('/api/history');

  const rows = useMemo(() => {
    const raw = history.data?.data ?? [];
    return raw.filter(r => !r.data_origin || r.data_origin === 'observed');
  }, [history.data]);

  const routes = useMemo(() => {
    return [...new Set(rows.map(r => r.route_short_name))].sort(compareRoutes);
  }, [rows]);

  const defaultRoute = useMemo(() => {
    if (routes.length === 0) return '';
    const pingsByRoute = new Map<string, number>();
    for (const row of rows) {
      pingsByRoute.set(
        row.route_short_name,
        (pingsByRoute.get(row.route_short_name) ?? 0) + (row.n_pings ?? 0)
      );
    }
    let best = routes[0];
    let maxPings = -1;
    for (const r of routes) {
      const pings = pingsByRoute.get(r) ?? 0;
      if (pings > maxPings) {
        maxPings = pings;
        best = r;
      }
    }
    return best;
  }, [routes, rows]);

  const [selected, setSelected] = useState('');
  const route = selected && routes.includes(selected) ? selected : defaultRoute;

  const routeRows = useMemo(() => {
    return rows.filter(r => r.route_short_name === route);
  }, [rows, route]);

  const totalPings = useMemo(() => {
    return routeRows.reduce((sum, r) => sum + (r.n_pings ?? 0), 0);
  }, [routeRows]);

  const {windows, first, last} = useMemo(() => {
    const times = routeRows
      .map(r => parseUtc(r.window_start)?.getTime())
      .filter((t): t is number => t !== undefined)
      .sort((a, b) => a - b);
    const uniqueTimes = [...new Set(times)];
    return {
      windows: uniqueTimes.length,
      first: uniqueTimes.length > 0 ? new Date(uniqueTimes[0]) : null,
      last: uniqueTimes.length > 0 ? new Date(uniqueTimes[uniqueTimes.length - 1]) : null,
    };
  }, [routeRows]);

  const grid = useMemo(() => hotspotGrid(rows), [rows]);

  return (
    <main className="dashboard" aria-labelledby="page-title">
      <PageHeader
        title="History"
        lede="Observed onboard load from automatic passenger counters, as the p90 for each 15-minute window."
      />
      <SourceLine kinds={['MEASURED · APC']} source={history.data?.source} />
      <Status loading={history.loading} error={history.error} />

      <label className="field" htmlFor="route-select">
        Route
        <select
          id="route-select"
          aria-label="Route"
          value={route}
          onChange={e => setSelected(e.target.value)}
        >
          {routes.map(r => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </label>

      {first && last ? (
        <p className="meta">
          {totalPings} pings across {windows} windows · {formatTime(first)}–{formatTime(last)} Central
        </p>
      ) : (
        <p className="meta">{totalPings} pings across {windows} windows</p>
      )}

      <LineChart
        series={historySeries(rows, route)}
        ariaLabel={`Observed p90 load for route ${route}, by 15-minute window`}
      />

      <p className="meta">
        Predicted-vs-observed comparison will appear once forecasts are archived; no historical forecasts exist yet.
      </p>

      <section className="section" aria-labelledby="recurring-hotspots-heading">
        <h2 id="recurring-hotspots-heading">Recurring hotspots</h2>
        <LoadLegend title="Observed p90 load" />
        <div className="table-scroll">
          <table className="data-table">
            <caption className="meta" style={{textAlign: 'left', marginBottom: '8px'}}>
              Most recent 12 windows. n = GPS pings in the window.
            </caption>
            <thead>
              <tr>
                <th scope="col">Route</th>
                {grid.slots.map(slot => (
                  <th key={slot.getTime()} scope="col">{formatTime(slot)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {grid.routes.map(r => (
                <tr key={r}>
                  <th scope="row">{r}</th>
                  {grid.slots.map(slot => {
                    const cell = grid.cells[r]?.[slot.getTime()];
                    const title = cell
                      ? `${formatDateTime(slot)} · n=${cell.n}`
                      : `${formatDateTime(slot)} · no data`;
                    return cloneElement(
                      LoadCell({value: cell?.p90, detail: cell ? `n=${cell.n}` : undefined}),
                      {key: slot.getTime(), title}
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
