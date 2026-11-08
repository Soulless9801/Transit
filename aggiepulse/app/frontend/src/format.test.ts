import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spreadOffsets, LOAD_SCALE, contrastRatio, loadStep, parseUtc, formatDateTime, formatTime, campusHour, pct, points, shiftLabel, significanceLevel, stopOptions, resolveStop, historySeries, hotspotGrid, directionLabels, compareRoutes, PAGES, pageFromHash, hashForPage, groupShowings} from './format.ts';

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
  assert.equal(resolveStop(options, ' unique ')?.id, '1');
  assert.equal(resolveStop(options, 'a'), null);
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
  assert.deepEqual(series.map(s => [s.direction, s.label]), [['a', 'Route service'], ['b', 'Route service']]);
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
  // Outside scheduled service, stale forecasts/pipeline are expected; only the collector is judged.
  const night = {...fresh, scoring_age_s: 30000, pipeline_lag_s: 4500, in_service: false, data_expected: false};
  assert.equal(freshnessLevel(night), 'idle');
  assert.equal(freshnessLevel({...night, bus_snapshot_age_s: 601}), 'red');
  assert.equal(freshnessLevel({...night, in_service: true}), 'ok');
  assert.equal(freshnessLevel({...night, data_expected: true}), 'red');
  assert.deepEqual([null, NaN, 0, 59, 60, 3600].map(ageLabel), ['unknown', 'unknown', '0 s', '59 s', '1 min', '1 hr']);
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


test('route chips sort naturally and always meet AA contrast, including unsafe GTFS preferences', async () => {
  const {safeTextColor} = await import('./format.ts');
  assert.deepEqual(['40-41','03-05','03','01-04','01','12','05'].sort(compareRoutes), ['01','01-04','03','03-05','05','12','40-41']);
  const {readFileSync} = await import('node:fs');
  const routes=JSON.parse(readFileSync(new URL('../public/static/routes.json',import.meta.url),'utf8'));
  for (const r of routes) assert.ok(contrastRatio('#'+r.route_color,safeTextColor(r.route_color,r.route_text_color)) >= 4.5, r.route_short_name);
  assert.equal(safeTextColor('500000','000000'),'#ffffff');
  assert.equal(safeTextColor('FFFFFF','FFFFFF'),'#000000');
  assert.equal(safeTextColor('500000','FFFFFF'),'#FFFFFF');
});

test('friendly times use Central dates across midnight and daylight-saving transitions', async () => {
  const {friendlyDate,serviceMessage} = await import('./format.ts');
  assert.equal(friendlyDate('2026-11-08T01:30:00Z',new Date('2026-11-08T02:00:00Z')), 'Today 7:30 PM');
  assert.equal(friendlyDate('2026-11-14T06:00:00Z',new Date('2026-11-08T02:00:00Z'),true),'Sat Nov 14');
  assert.equal(formatTime(new Date('2026-11-01T06:30:00Z')),'1:30 AM');
  assert.equal(formatTime(new Date('2026-11-01T07:30:00Z')),'1:30 AM');
  assert.equal(friendlyDate('bad'),'Time to be announced');
  assert.match(serviceMessage({in_service:false,next_scheduled_ts:'2026-11-08T15:00:00Z'} as any), /9:00 AM CT/);
});

test('stop labels disambiguate names without exposing internal IDs', async () => {
  const {readFileSync} = await import('node:fs');
  const features=JSON.parse(readFileSync(new URL('../public/static/stops.geojson',import.meta.url),'utf8')).features;
  const options=stopOptions(features);
  assert.equal(new Set(options.map(o => o.label)).size,options.length);
  for(const option of options) {
    assert.equal(resolveStop(options,option.label)?.id,option.id);
    assert.ok(!option.label.includes(option.id));
  }
  assert.equal(resolveStop(options,'MSC'),null);
});

test('factor explanations and notices hide implementation vocabulary', async () => {
  const {factorPhrase,cleanText,directionName,readableName} = await import('./format.ts');
  assert.equal(factorPhrase('Class change','class_change_15m',0.12),'↑ Classes letting out');
  assert.equal(factorPhrase('Event','event_uplift',-0.1),'↓ Less event activity nearby');
  assert.equal(factorPhrase('Weather','rain_mm',0.1),'↑ Rain');
  assert.equal(factorPhrase('Bunching','headway_cv',0.1),'↑ Buses bunching');
  assert.equal(factorPhrase('Time','hour',0.1),'↑ Usually busy at this time');
  assert.equal(cleanText('<p>Reed Arena (ID: 0053)&nbsp; &amp; MSC</p>'),'Reed Arena & MSC');
  assert.equal(directionName('0'),''); assert.equal(directionName('Direction 1'),'');
  assert.equal(directionName('MSC'),'MSC');
  assert.equal(readableName('rudder_auditorium'),'Rudder Auditorium');
});

test('hash routing and page resolution', () => {
  for (const page of PAGES) assert.equal(pageFromHash(hashForPage(page)), page);
  assert.equal(pageFromHash(''), 'Live map');
  assert.equal(pageFromHash('#'), 'Live map');
  assert.equal(pageFromHash('#Unknown'), 'Live map');
  assert.equal(pageFromHash('#FORECAST'), 'Forecast');
  assert.equal(pageFromHash(' #plan '), 'Plan my trip');
  assert.equal(hashForPage('Live map'), '#map');
});


test('nearby buses spread deterministically while distant buses stay put', () => {
  const check = (group: {id: string; x: number; y: number}[]) => {
    const offsets = spreadOffsets(group);
    assert.deepEqual(offsets, spreadOffsets([...group].reverse()));
    const positions = group.map(p => { const [x, y] = offsets.get(p.id)!; return {x: p.x + x, y: p.y + y}; });
    for (let i = 0; i < group.length; i++) {
      assert.notDeepEqual(offsets.get(group[i].id), [0, 0]);
      for (let j = 0; j < i; j++) assert.ok(Math.hypot(positions[i].x - positions[j].x, positions[i].y - positions[j].y) >= 43);
    }
    return offsets;
  };
  const pair = [{id: 'a', x: 0, y: 0}, {id: 'b', x: 10, y: 0}];
  const offsets = check(pair);
  assert.ok(offsets.get('a')![1] * offsets.get('b')![1] < 0);
  assert.deepEqual([...spreadOffsets([pair[0], {id: 'b', x: 500, y: 0}]).values()], [[0, 0], [0, 0]]);
  check([...pair, {id: 'c', x: 3, y: 2}]);
  check([{id: 'a', x: 0, y: 0}, {id: 'b', x: 40, y: 0}, {id: 'c', x: 80, y: 0}]);
  check(Array.from({length: 7}, (_, i) => ({id: String(i), x: i, y: i})));
  // a and b form a ring whose lower slot lands on c (50 px away); the groups merge instead of overlapping.
  check([{id: 'a', x: 0, y: 0}, {id: 'b', x: 10, y: 0}, {id: 'c', x: 0, y: 50}]);
});

test('groupShowings merges repeat showings preserving order and first item', () => {
  // same title and venue with two dates -> one entry with 2 showings in order
  const e1 = {title: 'OPAS presents A Beautiful Noise', venue_key: 'rudder', start_ts: '2026-11-10T19:30:00Z'};
  const e2 = {title: 'OPAS presents A Beautiful Noise', venue_key: 'rudder', start_ts: '2026-11-11T19:30:00Z'};
  const r1 = groupShowings([e1, e2]);
  assert.equal(r1.length, 1);
  assert.deepEqual(r1[0].showings, ['2026-11-10T19:30:00Z', '2026-11-11T19:30:00Z']);

  // same title at different venues -> two entries
  const e3 = {title: 'OPAS presents A Beautiful Noise', venue_key: 'kyle', start_ts: '2026-11-12T19:30:00Z'};
  const r2 = groupShowings([e1, e3]);
  assert.equal(r2.length, 2);
  assert.deepEqual(r2[0].showings, ['2026-11-10T19:30:00Z']);
  assert.deepEqual(r2[1].showings, ['2026-11-12T19:30:00Z']);

  // title differing only in case or whitespace -> merged
  const e4 = {title: '  opas   presents a beautiful noise  ', venue_key: 'rudder', start_ts: '2026-11-10T19:30:00Z'};
  const e5 = {title: 'OPAS PRESENTS A BEAUTIFUL NOISE', venue_key: 'RUDDER', start_ts: '2026-11-11T19:30:00Z'};
  const r3 = groupShowings([e4, e5]);
  assert.equal(r3.length, 1);
  assert.deepEqual(r3[0].showings, ['2026-11-10T19:30:00Z', '2026-11-11T19:30:00Z']);

  // group order follows first appearance
  const a1 = {title: 'Alpha', venue_key: 'v1', start_ts: 't1'};
  const b1 = {title: 'Beta', venue_key: 'v2', start_ts: 't2'};
  const a2 = {title: 'Alpha', venue_key: 'v1', start_ts: 't3'};
  const r4 = groupShowings([a1, b1, a2]);
  assert.equal(r4.length, 2);
  assert.equal(r4[0].title, 'Alpha');
  assert.deepEqual(r4[0].showings, ['t1', 't3']);
  assert.equal(r4[1].title, 'Beta');
  assert.deepEqual(r4[1].showings, ['t2']);

  // an exact duplicate row (same title, venue and time) is not listed as an extra showing
  assert.deepEqual(groupShowings([e1, {...e1}])[0].showings, ['2026-11-10T19:30:00Z']);
});

