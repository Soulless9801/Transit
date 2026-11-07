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
  const value = ts.trim().replace(/^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}(?:\.\d+)?)$/, '$1T$2Z');
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
export function stopOptions(features: {properties: {stop_id: string; stop_code?: string; stop_name: string}}[]): StopOption[] {
  return features.map(({properties: p}) => ({id: p.stop_id, code: p.stop_code || '', name: p.stop_name, label: `${p.stop_name} (${p.stop_code || p.stop_id})`}))
    .sort ( (a, b) => a.name.localeCompare(b.name) || a.code.localeCompare(b.code));
}
export function resolveStop(options: StopOption[], text: string): StopOption | null {
  const query = text.trim().toLowerCase();
  if (!query) return null;
  const equal = (s: string) => s.trim().toLowerCase() === query;
  const label = options.find(o => equal(o.label));
  if (label) return label;
  const id = options.find(o => equal(o.id) || (o.code && equal(o.code)));
  if (id) return id;
  const names = options.filter(o => equal(o.name));
  return names.length === 1 ? names[0] : null;
}
export type HistoryRow = {route_short_name: string; direction_id: string; window_start: string; p90_load_factor: number | null; avg_load_factor?: number | null; n_pings: number | null};
export type Series = {direction: string; label: string; points: {t: Date; p90: number; n: number}[]};
export function historySeries(rows: HistoryRow[], route: string): Series[] {
  const valid = rows.filter(row => row.route_short_name === route && row.p90_load_factor != null && parseUtc(row.window_start));
  const labels = directionLabels(valid.map(row => row.direction_id));
  return [...labels].map(([direction, label]) => ({direction, label, points: valid.filter(row => row.direction_id === direction).map(row => ({t: parseUtc(row.window_start)!, p90: row.p90_load_factor!, n: row.n_pings ?? 0})).sort((a, b) => a.t.getTime() - b.t.getTime())}));
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
