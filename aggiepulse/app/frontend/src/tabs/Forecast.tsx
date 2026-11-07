import {compareRoutes, directionLabels, pct, points} from '../format';
import {Badge, IntervalBar, LoadCell, LoadLegend, PageHeader, SourceLine, Status, useApi} from '../ui';

type Prediction = {
  route: string;
  direction: string;
  horizon_min: 15 | 30 | 60;
  pred_p10: number;
  pred_p50: number;
  pred_p90: number;
  p_crowd: number;
  top_factors?: {family: string; feature: string; value: number; contribution: number}[];
  source: 'databricks' | 'mock' | string;
  provenance?: string;
  training_data_origin?: string;
  model_version?: string;
};

type Route = {
  route_id?: string;
  route_short_name: string;
  route_long_name: string;
  route_color?: string;
};

export default function Forecast() {
  const predsApi = useApi<Prediction[]>('/api/predictions');
  const routesApi = useApi<Route[]>('/api/routes');

  const preds = predsApi.data ?? [];
  const routesData = routesApi.data ?? [];

  const routeMap = new Map<string, string>();
  for (const r of routesData) {
    routeMap.set(r.route_short_name, r.route_long_name);
  }

  const distinctRoutes = [...new Set(preds.map(p => p.route))].sort(compareRoutes);

  const directionMapByRoute = new Map<string, Map<string, string>>();
  for (const r of distinctRoutes) {
    const dirs = preds.filter(p => p.route === r).map(p => p.direction);
    directionMapByRoute.set(r, directionLabels(dirs));
  }

  const top30 = preds
    .filter(p => p.horizon_min === 30)
    .sort((a, b) => b.p_crowd - a.p_crowd || compareRoutes(a.route, b.route) || a.direction.localeCompare(b.direction))
    .slice(0, 5);

  const loading = predsApi.loading || routesApi.loading;
  const error = predsApi.error || routesApi.error;
  const empty = !loading && !error && preds.length === 0;

  return (
    <main className="dashboard" aria-labelledby="page-title">
      <PageHeader
        title="Forecast"
        lede="Predicted onboard load for every route over the next hour. A bus counts as crowded at 75% of capacity or more."
      />
      <SourceLine kinds={['PREDICTED']} source={preds[0]?.source} />
      <Status
        loading={loading}
        error={error}
        empty={empty}
        emptyText="No predictions available yet."
      />

      {preds.length > 0 && (
        <>
          <section className="section" aria-labelledby="next-hour-heading">
            <h2 id="next-hour-heading">Next hour by route</h2>
            <LoadLegend title="Median predicted load" />
            <div className="table-scroll">
              <table className="data-table">
                <caption className="visually-hidden">Next hour by route</caption>
                <thead>
                  <tr>
                    <th scope="col">Route</th>
                    <th scope="col">In 15 min</th>
                    <th scope="col">In 30 min</th>
                    <th scope="col">In 60 min</th>
                  </tr>
                </thead>
                <tbody>
                  {distinctRoutes.map(r => {
                    const longName = routeMap.get(r) || '';
                    return (
                      <tr key={r}>
                        <th scope="row">
                          {r}
                          <br />
                          <small style={{fontSize: '14px', fontWeight: 'normal'}}>{longName}</small>
                        </th>
                        {([15, 30, 60] as const).map(h => {
                          const matching = preds.filter(p => p.route === r && p.horizon_min === h);
                          const maxP50 = matching.length > 0 ? Math.max(...matching.map(p => p.pred_p50)) : null;
                          return <LoadCell key={h} value={maxP50} />;
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>

          <section className="section" aria-labelledby="crowding-risk-heading">
            <h2 id="crowding-risk-heading">Highest crowding risk in 30 minutes</h2>
            <div className="cards">
              {top30.map(p => {
                const dirLabel = directionMapByRoute.get(p.route)?.get(p.direction) ?? `Direction ${p.direction}`;
                const longName = routeMap.get(p.route) || '';
                const isSimulated = p.source === 'mock' || p.provenance === 'SIMULATED';
                const factors = [...(p.top_factors ?? [])].sort(
                  (a, b) => Math.abs(b.contribution) - Math.abs(a.contribution)
                );

                return (
                  <article key={`${p.route}-${p.direction}`}>
                    <h3>
                      Route {p.route} · {dirLabel}
                    </h3>
                    <p className="meta">{longName}</p>
                    <p>
                      <span className="big-stat">{pct(p.p_crowd)}</span>{' '}
                      <span className="stat-label">chance of being crowded</span>
                    </p>
                    <p>
                      Expected load {pct(p.pred_p50)}, likely between {pct(p.pred_p10)} and {pct(p.pred_p90)}
                    </p>
                    <IntervalBar p10={p.pred_p10} p50={p.pred_p50} p90={p.pred_p90} />
                    <h4>Why</h4>
                    <ul className="factor-list">
                      {factors.map((f, i) => (
                        <li key={`${f.family}-${f.feature}-${i}`}>
                          <span aria-hidden="true">{f.contribution >= 0 ? '▲' : '▼'}</span> {f.family} {points(f.contribution)}
                        </li>
                      ))}
                    </ul>
                    <p style={{display: 'flex', gap: '6px', flexWrap: 'wrap', margin: '12px 0 0'}}>
                      <Badge kind="PREDICTED" />
                      {isSimulated && <Badge kind="SIMULATED" />}
                      {p.training_data_origin && (
                        <Badge kind={`TRAINED ON ${p.training_data_origin.toUpperCase()}`} />
                      )}
                    </p>
                  </article>
                );
              })}
            </div>
          </section>
        </>
      )}
    </main>
  );
}
