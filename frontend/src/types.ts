export const metrics = {
  knee: { label: 'Just above kneecap', short: 'Above kneecap', unit: 'cm', color: '#327c66' },
  above5: { label: '5 cm above kneecap', short: '+5 cm', unit: 'cm', color: '#b3a16d' },
  above15: { label: '15 cm above kneecap', short: '+15 cm', unit: 'cm', color: '#8199b3' },
  flexion: { label: 'Knee flexion', short: 'Flexion', unit: '°', color: '#327c66' },
  extension: { label: 'Extension deficit', short: 'Extension deficit', unit: '°', color: '#8199b3' },
  pain_rest: { label: 'Pain at rest', short: 'At rest', unit: '/10', color: '#327c66' },
  pain_activity: { label: 'Pain with activity', short: 'With activity', unit: '/10', color: '#bb8766' },
  stiffness: { label: 'Morning stiffness', short: 'Stiffness', unit: 'min', color: '#8199b3' },
  exercise: { label: 'Rehab exercise', short: 'Rehab exercise', unit: 'min', color: '#327c66' },
  steps: { label: 'Daily steps', short: 'Steps', unit: 'steps', color: '#8199b3' },
  sleep: { label: 'Sleep', short: 'Sleep', unit: 'hrs', color: '#b3a16d' },
  instability: { label: 'Giving-way episodes', short: 'Giving way', unit: 'episodes', color: '#bb8766' },
};
export type Metric = keyof typeof metrics;
export type Entry = { id: string; date: string; notes: string; source: 'manual' | 'telegram' | 'api' }
  & Record<Metric | 'other_knee' | 'other_above5' | 'other_above15', number | null>;
export type Profile = { name: string; start_date: string | null; side: 'left' | 'right' | 'unspecified'; context: string; timezone: string };
export type Point = { x: number; y: number };
export type PhotoMode = 'flexion' | 'extension' | 'unclassified';
export type ExtensionDirection = 'deficit' | 'hyperextension' | '';
export type PhotoAnalysis = { id?: string; status: 'queued' | 'running' | 'ready' | 'failed' | 'needs_side' | 'disabled'; side?: 'left' | 'right'; points?: Point[]; angle?: number; confidence?: number; model?: string; model_version?: string; detail?: string };
export type PhotoMeasurement = { entry_id: string; date: string; value: number; metric?: 'flexion' | 'extension'; extension_direction?: ExtensionDirection; points: Point[]; side: 'left' | 'right'; reviewed_at: number; method: 'manual' | 'assisted'; model?: string; model_version?: string };
export type Photo = { id: string; date: string; caption: string; source?: 'manual' | 'telegram' | 'api'; side?: 'left' | 'right' | 'unspecified'; measurement_type?: PhotoMode; extension_direction?: ExtensionDirection | null; measurement_value?: number | null; points?: Point[]; angle?: number | null; analysis?: PhotoAnalysis; measurements?: PhotoMeasurement[] };
export type IntegrationStatus = { pose: { configured: boolean; available: boolean }; telegram: { online: boolean; username?: string; last_seen?: number } };
export type EntryPreview = { entry: Entry; existing: Entry | null; version: string };
export const emptyProfile: Profile = { name: '', start_date: null, side: 'unspecified', context: '', timezone: 'Asia/Singapore' };
export function today(timezone = 'Asia/Singapore') { return new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date()); }
export function dateLabel(value: string, full = false) { return new Date(value + 'T12:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: full ? 'long' : 'short', ...(full ? { year: 'numeric' as const } : {}) }); }
export function dayDiff(a: string, b: string) { return Math.round((Date.parse(a) - Date.parse(b)) / 86400000); }
export function fmt(n: number | null | undefined, digits = 1) { return n == null ? '—' : Number(n.toFixed(digits)).toLocaleString('en-GB'); }
export function makeEntry(date: string): Omit<Entry, 'id'> { return { date, notes: '', source: 'manual', ...Object.fromEntries([...Object.keys(metrics), 'other_knee', 'other_above5', 'other_above15'].map(k => [k, null])) } as Omit<Entry, 'id'>; }
export async function api<T>(path: string, method = 'GET', data?: unknown, headers: Record<string, string> = {}): Promise<T> {
  const res = await fetch('/api' + path, { method, headers: { 'X-Stride-Request': '1', ...(data && !(data instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), ...headers }, body: data instanceof FormData ? data : data ? JSON.stringify(data) : undefined });
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith('/auth/')) window.dispatchEvent(new Event('stride-session-expired'));
    const body = await res.json().catch(() => ({ detail: 'The server could not complete this request.' }));
    const detail = typeof body.detail === 'string' ? body.detail : body.detail?.[0]?.msg || 'Check the values and try again.';
    throw new Error(detail);
  }
  return res.status === 204 ? undefined as T : res.json();
}
export function sampleEntries(end: string): Entry[] {
  return Array.from({ length: 19 }, (_, i) => {
    const d = new Date(end + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() - (18 - i) * 3);
    const wave = Math.sin(i * 1.8) * 0.18;
    return { ...makeEntry(d.toISOString().slice(0, 10)), id: 'sample-' + i,
      knee: +(42.1 - i * .12 + wave).toFixed(1), above5: +(43.3 - i * .065 + wave).toFixed(1), above15: +(47.1 + i * .025 + wave).toFixed(1),
      other_knee: 38.5, other_above5: 41.5, other_above15: 49.2, flexion: Math.round(75 + i * 2.5), extension: Math.max(0, +(9 - i * .5).toFixed(1)),
      pain_rest: Math.max(0, Math.round(5 - i * .23)), pain_activity: Math.max(1, Math.round(7 - i * .22)), exercise: 15 + i * 2,
      stiffness: Math.max(5, 40 - i * 2), sleep: +(6.5 + Math.sin(i) * .7).toFixed(1), steps: 900 + i * 180, instability: 0,
      notes: i === 18 ? 'A little stiff this morning. Measurements taken before exercises, at the same time as usual.' : i === 16 ? 'Physio check-in today. Added notes from our session to my care plan.' : '' };
  });
}
