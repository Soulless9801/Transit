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
