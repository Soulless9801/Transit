import {test} from 'node:test';
import assert from 'node:assert/strict';
import {LOAD_SCALE, contrastRatio, loadStep, parseUtc, formatDateTime, formatTime, campusHour, pct, points, shiftLabel, significanceLevel, stopOptions, resolveStop, historySeries, hotspotGrid, directionLabels, compareRoutes} from './format.ts';

test('load scale and contrast', () => {
  for (const step of LOAD_SCALE) assert.ok(contrastRatio(step.bg, step.fg) >= 4.5);
  assert.ok(Math.abs(contrastRatio('#ffffff', '#000000') - 21) < 0.001);
  for (const [value, index] of [[0, 0], [0.19, 0], [0.2, 1], [0.749, 3], [0.75, 4], [2, 4], [-1, 0]]) assert.equal(loadStep(value), LOAD_SCALE[index]);
  assert.equal(loadStep(null), null);
  assert.equal(loadStep(NaN), null);
});

test('dates and display values', () => {
  const date = parseUtc('2026-11-09 00:00:00')!;
  assert.equal(date.getTime(), parseUtc('2026-11-09T00:00:00+00:00')!.getTime());
  assert.equal(date.getTime(), parseUtc('2026-11-09T00:00:00')!.getTime());
  for (const value of ['', null, 'garbage']) assert.equal(parseUtc(value), null);
  assert.equal(formatDateTime(date), 'Sun, Nov 8, 6:00 PM');
  assert.equal(formatTime(date), '6:00 PM');
  assert.equal(campusHour(new Date('2026-11-09T00:30:00Z')), 18);
  assert.equal(pct(null), '—'); assert.equal(pct(NaN), '—'); assert.equal(pct(0.126), '13%');
  assert.equal(points(0.12), '+12 pts'); assert.equal(points(-0.04), '−4 pts'); assert.equal(points(0), '0 pts');
  assert.equal(shiftLabel(-10), 'Leave 10 min earlier'); assert.equal(shiftLabel(0), 'Leave now'); assert.equal(shiftLabel(20), 'Leave 20 min later');
  assert.deepEqual([9.9, 10, 13.9, 14].map(significanceLevel), ['Moderate', 'High', 'High', 'Major']);
});

test('stop resolution and ordering', () => {
  const options = stopOptions([{properties: {stop_id: '1', stop_code: 'A', stop_name: 'Unique'}}, {properties: {stop_id: '2', stop_code: 'B', stop_name: 'Shared'}}, {properties: {stop_id: '3', stop_code: 'C', stop_name: 'Shared'}}]);
  assert.equal(resolveStop(options, ' unique (a) ')?.id, '1');
  assert.equal(resolveStop(options, 'a')?.id, '1');
  assert.equal(resolveStop(options, 'Unique')?.id, '1');
  assert.equal(resolveStop(options, 'shared'), null);
  assert.equal(resolveStop(options, 'unknown'), null);
  assert.deepEqual([...directionLabels(['b', 'a', 'b']).values()], ['Direction 1', 'Direction 2']);
  assert.deepEqual(['10', '2', '1'].sort(compareRoutes), ['1', '2', '10']);
});

test('history series and hotspot grid', () => {
  const row = (route: string, direction: string, hour: string, p90: number | null, n: number | null) => ({route_short_name: route, direction_id: direction, window_start: `2026-11-09T${hour}:00:00Z`, p90_load_factor: p90, n_pings: n});
  const rows = [row('1', 'b', '02', 0.3, 2), row('1', 'a', '01', 0.4, 3), row('1', 'a', '02', 0.8, 4), row('1', 'b', '03', 0.6, 5), row('2', 'a', '03', 0.9, 6), row('1', 'a', '03', null, 7)];
  const series = historySeries(rows, '1');
  assert.deepEqual(series.map(s => [s.direction, s.label]), [['a', 'Direction 1'], ['b', 'Direction 2']]);
  assert.deepEqual(series.map(s => s.points.map(p => p.t.getUTCHours())), [[1, 2], [2, 3]]);
  const grid = hotspotGrid(rows, 2);
  assert.deepEqual(grid.slots.map(s => s.getUTCHours()), [2, 3]);
  assert.deepEqual(grid.cells['1'][grid.slots[0].getTime()], {p90: 0.8, n: 6});
});

test('map route colors use measured ring thresholds and busiest direction', async () => {
  const {crowdColor, routeLoad} = await import('./format.ts');
  assert.equal(crowdColor(0.499), '#238451');
  assert.equal(crowdColor(0.5), '#a96800');
  assert.equal(crowdColor(0.75), '#cf3f46');
  assert.equal(crowdColor(null), '#7b8490');
  assert.equal(crowdColor(Infinity), '#7b8490');
  const prediction = (route: string, horizon: number, load: number) => ({route, direction: '0', horizon_min: horizon, pred_p10: 0, pred_p50: load, pred_p90: 1});
  const rows = [prediction('12', 30, 0.4), prediction('12', 30, 0.8), prediction('12', 15, 1)];
  assert.equal(routeLoad(rows, '12'), 0.8);
  assert.equal(routeLoad(rows, '99'), null);
});

test('replay never shows future or expired positions and chooses latest per bus', async () => {
  const {replayFrame} = await import('./format.ts');
  const row = (vehicle_id: string, minute: string) => ({vehicle_id, ts_gps: `2026-11-07T20:${minute}:00Z`});
  const rows = [row('a', '01'), row('a', '02'), row('b', '03'), row('c', '00')];
  assert.deepEqual(replayFrame(rows, Date.parse('2026-11-07T20:02:30Z')), [rows[1]]);
  assert.deepEqual(replayFrame(rows, Date.parse('2026-11-07T20:06:00Z')), []);
  assert.deepEqual(replayFrame(rows, Date.parse('2026-11-07T19:59:00Z')), []);
});

test('freshness status is explicit for unknown, delayed, stale and fallback data', async () => {
  const {ageLabel, freshnessLevel} = await import('./format.ts');
  const fresh = {bus_snapshot_age_s: 30, bus_source: 'hot-path', last_scoring_ts: '2026-11-07T20:00:00Z', scoring_age_s: 900, pipeline_lag_s: 1200};
  assert.equal(freshnessLevel(fresh), 'ok');
  assert.equal(freshnessLevel({...fresh, bus_snapshot_age_s: 181}), 'amber');
  assert.equal(freshnessLevel({...fresh, scoring_age_s: 1801}), 'amber');
  assert.equal(freshnessLevel({...fresh, pipeline_lag_s: 2401}), 'amber');
  for (const values of [{bus_snapshot_age_s: 601}, {scoring_age_s: 3601}, {pipeline_lag_s: 7201}, {bus_source: 'local-snapshot'}, {pipeline_lag_s: null}]) assert.equal(freshnessLevel({...fresh, ...values}), 'red');
  assert.deepEqual([null, NaN, 0, 59, 60, 3600].map(ageLabel), ['unknown', 'unknown', '0s', '59s', '1m', '1.0h']);
});

test('comparison filters observed matches and computes inclusive interval coverage', async () => {
  const {comparisonRows, comparisonMetrics} = await import('./format.ts');
  const row = {route_short_name: '12', direction_id: '0', horizon_min: 30, target_window_start: '2026-11-07T20:00:00Z', pred_p10: 0.2, pred_p50: 0.5, pred_p90: 0.8, actual_p90_load: 0.2, actual_n_pings: 8, actual_data_origin: 'observed'};
  const rows = [row, {...row, actual_p90_load: 0.8}, {...row, actual_p90_load: 1}, {...row, actual_data_origin: 'simulated'}, {...row, actual_n_pings: 0}, {...row, horizon_min: 60}, {...row, actual_p90_load: null}, {...row, route_short_name: '99'}];
  const kept = comparisonRows(rows, '12', 30);
  assert.equal(kept.length, 3);
  const metrics = comparisonMetrics(kept);
  assert.equal(metrics.n, 3);
  assert.ok(Math.abs(metrics.mae! - 1.1 / 3) < 1e-10);
  assert.equal(metrics.coverage, 2 / 3);
  assert.deepEqual(comparisonMetrics([]), {n: 0, mae: null, coverage: null});
});
