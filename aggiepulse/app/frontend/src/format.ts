export const PAGES = ['Live map', 'Forecast', 'Events', 'Plan my trip', 'History'] as const;
export type Page = typeof PAGES[number];

const PAGE_TO_HASH: Record<Page, string> = {
  'Live map': '#map',
  'Forecast': '#forecast',
  'Events': '#events',
  'Plan my trip': '#plan',
  'History': '#history',
};

const HASH_TO_PAGE: Record<string, Page> = {
  map: 'Live map',
  forecast: 'Forecast',
  events: 'Events',
  plan: 'Plan my trip',
  history: 'History',
};

export function pageFromHash(hash: string): Page {
  const slug = (hash || '').trim().replace(/^#/, '').trim().toLowerCase();
  return HASH_TO_PAGE[slug] ?? 'Live map';
}

export function hashForPage(page: Page): string {
  return PAGE_TO_HASH[page] ?? '#map';
}

export const CAMPUS_TZ = 'America/Chicago';
export type LoadStep = {min: number; label: string; bg: string; fg: string};
export const LOAD_SCALE: LoadStep[] = [
  {min: 0, label: 'Under 20%', bg: '#f7eeee', fg: '#24252a'},
  {min: 0.2, label: '20–39%', bg: '#e9caca', fg: '#24252a'},
  {min: 0.4, label: '40–59%', bg: '#d29a9a', fg: '#24252a'},
  {min: 0.6, label: '60–74%', bg: '#a52d2d', fg: '#ffffff'},
  {min: 0.75, label: '75%+ · crowded', bg: '#500000', fg: '#ffffff'},
];
export function loadStep(v: number | null | undefined): LoadStep | null {
  if (v == null || Number.isNaN(v)) return null;
  const value = Math.max(0, Math.min(1, v));
  return LOAD_SCALE.filter(step => step.min <= value).at(-1) ?? null;
}
export function contrastRatio(a: string, b: string): number {
  const luminance = (hex: string) => {
    const channels = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map(c => c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
  };
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + 0.05) / (dark + 0.05);
}
export function parseUtc(ts: string | null | undefined): Date | null {
  if (!ts?.trim()) return null;
  const value = ts.trim().replace(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2}(?:\.\d+)?)$/, '$1T$2Z');
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}
export function formatDateTime(d: Date): string {
  return new Intl.DateTimeFormat('en-US', {timeZone: CAMPUS_TZ, weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}).format(d).replace(/\u202f/g, ' ');
}
export function formatTime(d: Date): string {
  return new Intl.DateTimeFormat('en-US', {timeZone: CAMPUS_TZ, hour: 'numeric', minute: '2-digit'}).format(d).replace(/\u202f/g, ' ');
}
export function campusHour(now: Date = new Date()): number {
  return Number(new Intl.DateTimeFormat('en-US', {timeZone: CAMPUS_TZ, hour: 'numeric', hourCycle: 'h23'}).format(now));
}
export function pct(v: number | null | undefined): string {
  return v == null || Number.isNaN(v) ? '—' : `${Math.round(v * 100)}%`;
}
export function points(contribution: number): string {
  const value = Math.round(Math.abs(contribution) * 100);
  return `${contribution > 0 ? '+' : contribution < 0 ? '−' : ''}${value} pts`;
}
export function shiftLabel(shift: number): string {
  return shift === 0 ? 'Leave now' : `Leave ${Math.abs(shift)} min ${shift < 0 ? 'earlier' : 'later'}`;
}
export function significanceLevel(s: number): 'Moderate' | 'High' | 'Major' {
  return s < 10 ? 'Moderate' : s < 14 ? 'High' : 'Major';
}
export function directionLabels(values: string[]): Map<string, string> {
  return new Map([...new Set(values)].sort((a, b) => a.localeCompare(b)).map((value, i) => [value, `Direction ${i + 1}`]));
}
export function compareRoutes(a: string, b: string): number {
  return a.localeCompare(b, undefined, {numeric: true});
}
export type StopOption = {id: string; code: string; name: string; label: string};
export function stopOptions(features: {geometry?: {coordinates: number[]}; properties: {stop_id: string; stop_code?: string; stop_name: string; routes?: string[]}}[]): StopOption[] {
  return features.map(({properties: p, geometry}) => {
    const duplicates = features.filter(f => f.properties.stop_name === p.stop_name);
    const routes = [...(p.routes ?? [])].sort(compareRoutes).join(', ');
    const sameRoutes = duplicates.filter(f => [...(f.properties.routes ?? [])].sort(compareRoutes).join(', ') === routes);
    // Same-name bays with the same routes are distinguished by their geographic position.
    const position = sameRoutes.length > 1 && geometry ? (geometry.coordinates[1] === Math.max(...sameRoutes.map(f => f.geometry?.coordinates[1] ?? -Infinity)) ? 'north side' : 'south side') : '';
    return {id: p.stop_id, code: p.stop_code || '', name: p.stop_name,
      label: p.stop_name + (duplicates.length > 1 ? ` · ${routes ? 'Routes ' + routes : 'Campus stop'}${position ? ' · ' + position : ''}` : '')};
  }).sort((a, b) => a.label.localeCompare(b.label, undefined, {numeric: true}));
}
export function resolveStop(options: StopOption[], text: string): StopOption | null {
  const query = text.trim().toLowerCase();
  if (!query) return null;
  const labels = options.filter(o => o.label.toLowerCase() === query);
  if (labels.length === 1) return labels[0];
  const names = options.filter(o => o.name.toLowerCase() === query);
  return names.length === 1 ? names[0] : null;
}
export type Route = {route_id?: string; route_short_name: string; route_long_name: string; route_color?: string; route_text_color?: string};
export function routeColor(color?: string): string {
  return color && /^#?[0-9a-f]{6}$/i.test(color) ? '#' + color.replace('#', '') : '#500000';
}
export function safeTextColor(background: string, preferred?: string): string {
  const bg = routeColor(background), text = preferred ? routeColor(preferred) : '';
  if (text && contrastRatio(bg, text) >= 4.5) return text;
  return contrastRatio(bg, '#000000') >= contrastRatio(bg, '#ffffff') ? '#000000' : '#ffffff';
}
export function friendlyDate(value: string | Date, now = new Date(), dateOnly = false): string {
  const date = value instanceof Date ? value : parseUtc(value);
  if (!date) return 'Time to be announced';
  const day = (d: Date) => new Intl.DateTimeFormat('en-CA', {timeZone: CAMPUS_TZ}).format(d);
  const label = day(date) === day(now) ? 'Today' : new Intl.DateTimeFormat('en-US', {timeZone: CAMPUS_TZ, weekday: 'short', month: 'short', day: 'numeric'}).format(date).replace(',', '');
  return dateOnly ? label : `${label} ${formatTime(date)}`;
}
export function readableName(value?: string): string {
  return (value || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}
export function directionName(value?: string): string {
  return !value || /^(?:direction\s*)?\d+$/i.test(value) ? '' : value.replace(/_/g, ' ');
}
export function factorPhrase(family: string, feature = '', contribution = 0): string {
  const key = (family + ' ' + feature).toLowerCase();
  const phrase = /class/.test(key) ? (contribution < 0 ? 'Fewer classes letting out' : 'Classes letting out')
    : /event/.test(key) ? (contribution < 0 ? 'Less event activity nearby' : 'Event nearby')
    : /rain|precip/.test(key) ? 'Rain'
    : /weather/.test(key) ? 'Weather'
    : /bunch|headway/.test(key) ? (contribution < 0 ? 'Buses spaced apart' : 'Buses bunching')
    : /recent|load now|lag/.test(key) ? (contribution < 0 ? 'Buses have been quieter' : 'Buses have been busy')
    : /time|season|calendar|baseline/.test(key) ? (contribution < 0 ? 'Usually quieter at this time' : 'Usually busy at this time')
    : /delay|traffic/.test(key) ? 'Traffic delays' : 'Usual travel patterns';
  return `${contribution < 0 ? '↓' : '↑'} ${phrase}`;
}
export function serviceMessage(f?: Freshness, noun = 'Forecasts'): string {
  if (f?.in_service === false) return `No buses are scheduled right now.${f.next_scheduled_ts ? ` ${noun} resume ${friendlyDate(f.next_scheduled_ts)} CT.` : ' Check back when bus service resumes.'}`;
  if (f?.data_expected === false) return 'Bus service is starting. Fresh readings and forecasts usually appear within 20 min.';
  return 'Fresh data is not available yet. Check back after buses have been running for about 20 min.';
}
export function cleanText(text: string): string {
  return text.replace(/<[^>]*>/g, ' ').replace(/&nbsp;|&#160;/gi, ' ').replace(/&amp;/gi, '&').replace(/&quot;/gi, '"').replace(/&#39;|&apos;/gi, "'").replace(/\(ID:\s*[^)]+\)/gi, '').replace(/\s+/g, ' ').trim();
}
export type HistoryRow = {route_short_name: string; direction_id: string; direction_name?: string; window_start: string; p90_load_factor: number | null; avg_load_factor?: number | null; n_pings: number | null};
export type Series = {direction: string; label: string; points: {t: Date; p90: number; n: number}[]};
export function historySeries(rows: HistoryRow[], route: string): Series[] {
  const valid = rows.filter(row => row.route_short_name === route && row.p90_load_factor != null && parseUtc(row.window_start));
  const labels = directionLabels(valid.map(row => row.direction_id));
  return [...labels].map(([direction, label]) => ({direction, label: directionName(valid.find(r => r.direction_id === direction)?.direction_name) || 'Route service', points: valid.filter(row => row.direction_id === direction).map(row => ({t: parseUtc(row.window_start)!, p90: row.p90_load_factor!, n: row.n_pings ?? 0})).sort((a, b) => a.t.getTime() - b.t.getTime())}));
}
export type HotspotGrid = {slots: Date[]; routes: string[]; cells: Record<string, Record<number, {p90: number; n: number}>>};
export function hotspotGrid(rows: HistoryRow[], maxSlots = 12): HotspotGrid {
  const times = [...new Set(rows.map(row => parseUtc(row.window_start)?.getTime()).filter((t): t is number => t !== undefined))].sort((a, b) => a - b);
  const slots = times.slice(-Math.max(0, maxSlots)).map(t => new Date(t));
  if (maxSlots <= 0) slots.length = 0;
  const kept = new Set(slots.map(slot => slot.getTime()));
  const routes = [...new Set(rows.map(row => row.route_short_name))].sort(compareRoutes);
  const cells: HotspotGrid['cells'] = {};
  for (const row of rows) {
    const time = parseUtc(row.window_start)?.getTime();
    if (time === undefined || !kept.has(time) || row.p90_load_factor == null) continue;
    const routeCells = cells[row.route_short_name] ??= {};
    const previous = routeCells[time];
    routeCells[time] = {p90: previous ? Math.max(previous.p90, row.p90_load_factor) : row.p90_load_factor, n: (previous?.n ?? 0) + (row.n_pings ?? 0)};
  }
  return {slots, routes, cells};
}

export const MAP_LOAD_SCALE = [
  {color: '#238451', label: 'Under 50%'},
  {color: '#a96800', label: '50–74%'},
  {color: '#cf3f46', label: '75%+'},
  {color: '#7b8490', label: 'No data'},
];
export function crowdColor(value: number | null | undefined): string {
  return MAP_LOAD_SCALE[value == null || !Number.isFinite(value) ? 3 : value < 0.5 ? 0 : value < 0.75 ? 1 : 2].color;
}
export type Prediction = {
  route: string; direction: string; direction_name?: string; horizon_min: number;
  pred_p10: number; pred_p50: number; pred_p90: number; provenance?: string;
  scoring_ts?: string; training_data_origin?: string;
  top_factors?: {family: string; feature: string; contribution: number}[];
};
export function routeLoad(predictions: Prediction[], route: string, horizon = 30): number | null {
  const values = predictions.filter(p => p.route === route && p.horizon_min === horizon)
    .map(p => p.pred_p50).filter(v => v != null && Number.isFinite(v));
  return values.length ? Math.max(...values) : null;
}
export type ReplayPosition = {vehicle_id: string; ts_gps: string | null};
export function replayFrame<T extends ReplayPosition>(rows: T[], cursor: number): T[] {
  const latest = new Map<string, {time: number; row: T}>();
  for (const row of rows) {
    const time = parseUtc(row.ts_gps)?.getTime();
    if (time == null || time > cursor || cursor - time > 120000) continue;
    if (!latest.has(row.vehicle_id) || latest.get(row.vehicle_id)!.time < time) latest.set(row.vehicle_id, {time, row});
  }
  return [...latest.values()].map(v => v.row);
}
export function ageLabel(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return 'unknown';
  return seconds < 60 ? `${Math.floor(Math.max(0, seconds))} s` : seconds < 3600 ? `${Math.floor(seconds / 60)} min` : `${Math.floor(seconds / 3600)} hr`;
}
export type Freshness = {
  bus_snapshot_age_s: number | null; bus_source: string; last_scoring_ts: string | null;
  scoring_age_s: number | null; pipeline_lag_s: number | null;
  in_service?: boolean | null; data_expected?: boolean | null; next_scheduled_ts?: string | null;
};
export function freshnessLevel(f: Freshness): 'ok' | 'idle' | 'amber' | 'red' {
  // The live bus snapshot proves the collector is alive, so it's judged at any hour.
  if (f.bus_snapshot_age_s == null || f.bus_source === 'local-snapshot') return 'red';
  // Outside scheduled service (or its first 20 min) no new pings or forecasts are expected.
  if (f.data_expected === false) {
    if (f.bus_snapshot_age_s > 600) return 'red';
    if (f.bus_snapshot_age_s > 180) return 'amber';
    return f.in_service ? 'ok' : 'idle';
  }
  if (f.scoring_age_s == null || f.pipeline_lag_s == null ||
      f.bus_source === 'local-snapshot' || f.bus_snapshot_age_s > 600 || f.scoring_age_s > 3600 || f.pipeline_lag_s > 7200) return 'red';
  return f.bus_snapshot_age_s > 180 || f.scoring_age_s > 1800 || f.pipeline_lag_s > 2400 ? 'amber' : 'ok';
}
export type ComparisonRow = {
  route_short_name: string; direction_id: string; direction_name?: string; horizon_min: number;
  target_window_start: string; pred_p10: number; pred_p50: number; pred_p90: number;
  actual_p90_load: number | null; actual_n_pings: number; actual_data_origin: string;
};
export function comparisonRows(rows: ComparisonRow[], route: string, horizon: number): ComparisonRow[] {
  return rows.filter(r => r.route_short_name === route && r.horizon_min === horizon &&
    r.actual_data_origin === 'observed' && r.actual_n_pings > 0 && parseUtc(r.target_window_start) &&
    [r.actual_p90_load, r.pred_p10, r.pred_p50, r.pred_p90].every(v => v != null && Number.isFinite(v)))
    .sort((a, b) => parseUtc(a.target_window_start)!.getTime() - parseUtc(b.target_window_start)!.getTime());
}
export function comparisonMetrics(rows: ComparisonRow[]): {n: number; mae: number | null; coverage: number | null} {
  const n = rows.length;
  return {n, mae: n ? rows.reduce((s, r) => s + Math.abs(r.pred_p50 - r.actual_p90_load!), 0) / n : null,
    coverage: n ? rows.filter(r => r.actual_p90_load! >= r.pred_p10 && r.actual_p90_load! <= r.pred_p90).length / n : null};
}

export function spreadOffsets(points: {id: string; x: number; y: number}[], thresholdPx = 44, gapPx = 44): Map<string, [number, number]> {
  // Single-linkage groups in screen pixels; each group of n > 1 is laid out on a ring around its centroid
  // with adjacent slots >= gapPx apart. If two groups' rings collide, they are merged and laid out again.
  const pts = [...points].sort((a, b) => a.id.localeCompare(b.id));
  const parent = pts.map((_, i) => i);
  const find = (i: number): number => parent[i] === i ? i : (parent[i] = find(parent[i]));
  const union = (a: number, b: number) => { const ra = find(a), rb = find(b); if (ra !== rb) parent[Math.max(ra, rb)] = Math.min(ra, rb); };
  for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++) if (Math.hypot(pts[i].x - pts[j].x, pts[i].y - pts[j].y) < thresholdPx) union(i, j);
  const layout = () => {
    const groups = new Map<number, number[]>();
    pts.forEach((_, i) => { const r = find(i); groups.set(r, [...(groups.get(r) ?? []), i]); });
    const pos = pts.map(p => [p.x, p.y]);
    for (const group of groups.values()) {
      if (group.length === 1) continue;
      const cx = group.reduce((sum, i) => sum + pts[i].x, 0) / group.length;
      const cy = group.reduce((sum, i) => sum + pts[i].y, 0) / group.length;
      const radius = Math.max(gapPx / 2, gapPx / (2 * Math.sin(Math.PI / group.length)));
      group.forEach((index, i) => { const angle = 2 * Math.PI * i / group.length - Math.PI / 2; pos[index] = [cx + radius * Math.cos(angle), cy + radius * Math.sin(angle)]; });
    }
    return pos;
  };
  let pos = layout();
  for (let merged = true; merged;) {
    merged = false;
    for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++) {
      if (find(i) !== find(j) && Math.hypot(pos[i][0] - pos[j][0], pos[i][1] - pos[j][1]) < gapPx - 1) { union(i, j); merged = true; }
    }
    if (merged) pos = layout();
  }
  const offsets = new Map<string, [number, number]>();
  pts.forEach((p, i) => offsets.set(p.id, [Math.round(pos[i][0] - p.x), Math.round(pos[i][1] - p.y)]));
  return offsets;
}
