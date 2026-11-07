import { useState, type FormEvent } from 'react';
import { Check, Ruler, Heart, ArrowRight } from 'lucide-react';
import Modal from './Modal';
import { api, makeEntry, type Entry } from './types';

export default function EntryForm({ existing, date, close, saved }: { existing?: Entry; date: string; close: () => void; saved: () => Promise<void> }) {
  const [entry, setEntry] = useState<Omit<Entry, 'id'>>(existing ? (({ id: _, ...rest }) => rest)(existing) : makeEntry(date));
  const [tab, setTab] = useState('measurements'); const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const set = (key: string, value: unknown) => setEntry(v => ({ ...v, [key]: value }));
  function field(key: keyof typeof entry, label: string, unit: string, min: number, max: number, step = '0.1') {
    return <label className="field">{label}<div className="input-unit"><input type="number" inputMode={step === '1' ? 'numeric' : 'decimal'} min={min} max={max} step={step} value={entry[key] ?? ''} onChange={e => set(key, e.target.value === '' ? null : Number(e.target.value))}/><span>{unit}</span></div></label>;
  }
  async function submit(e: FormEvent) { e.preventDefault(); setBusy(true); setError(''); try { await api(existing ? '/entries/' + existing.id : '/entries', existing ? 'PUT' : 'POST', entry); await saved(); close(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  return <Modal title={existing ? 'Edit your entry' : 'A small check-in.'} subtitle="Record what you can. Every field is optional." close={close} wide><form onSubmit={submit}>
    <label className="field date-field">Entry date<input type="date" value={entry.date} max={date} required onChange={e => set('date', e.target.value)}/></label>
    <div className="form-tabs"><button type="button" className={tab === 'measurements' ? 'active' : ''} onClick={() => setTab('measurements')}><Ruler size={16}/> Measurements</button><button type="button" className={tab === 'wellbeing' ? 'active' : ''} onClick={() => setTab('wellbeing')}><Heart size={16}/> Symptoms & activity</button></div>
    <div className="form-body" hidden={tab !== 'measurements'}>
      <h3>Circumference <span>cm</span></h3><p className="field-help">Use the same landmarks, tape tension, leg position and time of day. Measure above the top edge of the kneecap consistently.</p>
      <div className="measurement-grid"><span/><span className="column-label">Recovering leg</span><span className="column-label">Other leg · optional</span>
        <span className="row-label">Just above kneecap</span>{field('knee', 'Recovering just above kneecap', 'cm', 5, 150)}{field('other_knee', 'Other just above kneecap', 'cm', 5, 150)}
        <span className="row-label">5 cm above</span>{field('above5', 'Recovering +5 cm', 'cm', 5, 150)}{field('other_above5', 'Other +5 cm', 'cm', 5, 150)}
        <span className="row-label">15 cm above</span>{field('above15', 'Recovering +15 cm', 'cm', 5, 150)}{field('other_above15', 'Other +15 cm', 'cm', 5, 150)}</div>
      <h3>Range of motion</h3><div className="field-grid">{field('flexion', 'Flexion (bend)', '°', 0, 180)}{field('extension', 'Extension deficit', '°', -30, 90)}</div><p className="field-help">Extension: 0° = straight, positive = short of straight, negative = hyperextension. Only measure within your prescribed limits.</p>
    </div>
    <div className="form-body" hidden={tab !== 'wellbeing'}><h3>How did your knee feel?</h3><div className="field-grid">{field('pain_rest', 'Pain at rest', '/ 10', 0, 10, '1')}{field('pain_activity', 'Pain during activity', '/ 10', 0, 10, '1')}{field('stiffness', 'Morning stiffness', 'min', 0, 1440, '1')}{field('instability', 'Giving-way episodes', 'times', 0, 100, '1')}</div><p className="field-help">Pain: 0 = none, 10 = worst imaginable. Log symptoms from your usual activity; this is not a test to provoke pain.</p><h3>Your day</h3><div className="field-grid">{field('exercise', 'Rehab exercises', 'min', 0, 1440, '1')}{field('steps', 'Daily steps', 'steps', 0, 100000, '1')}{field('sleep', 'Sleep last night', 'hours', 0, 24)}</div></div>
    <label className="field notes-field">Anything to remember?<textarea rows={3} placeholder="Exercises, how your knee responded, or a note for your physio…" maxLength={5000} value={entry.notes} onChange={e => set('notes', e.target.value)}/></label>
    {error && <p className="error" role="alert">{error}</p>}<div className="modal-footer"><span><Check size={14}/> Saved privately on your server</span><button disabled={busy} className="button primary">{busy ? 'Saving…' : existing ? 'Save changes' : 'Save entry'}<ArrowRight size={16}/></button></div>
  </form></Modal>;
}
