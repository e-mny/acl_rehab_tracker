import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Camera, ChevronLeft, ChevronRight, ImagePlus, RefreshCw, Ruler, Save, Sparkles, Trash2, Upload } from 'lucide-react';
import Modal from './Modal';
import AngleTool from './AngleTool';
import { guideArms, kneeBend, signedValue } from './photoGeometry';
import { api, dateLabel, fmt, type EntryPreview, type ExtensionDirection, type IntegrationStatus, type Photo, type PhotoMode, type Point, type Profile } from './types';

type Preview = EntryPreview & { key: string };
const modeLabel = (mode: PhotoMode) => mode === 'extension' ? 'extension' : 'flexion';
const newKey = () => 'photo-review:' + Array.from(crypto.getRandomValues(new Uint8Array(16)), v => v.toString(16).padStart(2, '0')).join('');

export default function Photos({ date, side: profileSide, notify, entrySaved }: { date: string; side: Profile['side']; notify: (s: string) => void; entrySaved: () => Promise<void> }) {
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [selected, setSelected] = useState<Photo>();
  const [error, setError] = useState('');
  const [upload, setUpload] = useState(false);
  const [file, setFile] = useState<File>();
  const [caption, setCaption] = useState('');
  const [photoDate, setPhotoDate] = useState(date);
  const [side, setSide] = useState<'left' | 'right' | ''>('');
  const [mode, setMode] = useState<PhotoMode>('unclassified');
  const [direction, setDirection] = useState<ExtensionDirection>('');
  const [points, setPoints] = useState<Point[]>(guideArms());
  const [aligned, setAligned] = useState(false);
  const [size, setSize] = useState({ w: 1, h: 1 });
  const [sizeReady, setSizeReady] = useState(false);
  const [origin, setOrigin] = useState<string>();
  const [reviewed, setReviewed] = useState(false);
  const [preview, setPreview] = useState<Preview>();
  const [busy, setBusy] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [status, setStatus] = useState<IntegrationStatus>();
  const [batchSide, setBatchSide] = useState('');
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchSummary, setBatchSummary] = useState('');
  const [reviewBatch, setReviewBatch] = useState(false);
  const activeId = useRef<string | undefined>(undefined);
  const geometryDirty = useRef(false);
  const analysisSignature = useRef('');
  const visited = useRef(new Set<string>());
  const pending = photos.filter(p => ['queued','running'].includes(p.analysis?.status || '')).length;

  async function refresh() { const result = await api<Photo[]>('/photos'); setPhotos(result); return result; }
  useEffect(() => { refresh().catch(e => setError(e.message)); api<IntegrationStatus>('/integrations').then(setStatus).catch(() => {}); }, []);
  useEffect(() => { const timer = setInterval(() => { refresh().catch(() => {}); }, pending ? 1500 : 10000); return () => clearInterval(timer); }, [pending]);
  useEffect(() => {
    const fresh = photos.find(p => p.id === activeId.current);
    if (!fresh) return;
    setSelected(fresh);
    const signature = (fresh.analysis?.id || '') + ':' + fresh.analysis?.status;
    if (signature !== analysisSignature.current) {
      analysisSignature.current = signature;
      if (fresh.analysis?.status === 'ready' && fresh.analysis.points?.length === 3 && !geometryDirty.current) {
        setPoints(fresh.analysis.points); setAligned(true); setOrigin(fresh.analysis.id); setReviewed(false); setPreview(undefined);
      }
    }
  }, [photos]);

  function invalidate() { setReviewed(false); setPreview(undefined); setError(''); }
  function changePoints(next: Point[]) { geometryDirty.current = true; setPoints(next); setAligned(true); invalidate(); }
  function resetGuides() { geometryDirty.current = true; setPoints(guideArms()); setAligned(false); setOrigin(undefined); invalidate(); }
  function close() { if (busy) return; activeId.current = undefined; setSelected(undefined); setReviewBatch(false); }
  async function enqueue(p: Photo, chosenSide: string) {
    if (!['left','right'].includes(chosenSide)) return;
    try { await api('/photos/'+p.id+'/queue', 'POST', { side: chosenSide }); await refresh(); }
    catch (e) { if (activeId.current === p.id) setError((e as Error).message); }
  }
  function open(p: Photo) {
    activeId.current = p.id;
    const saved = p.points?.length === 3;
    const proposal = p.analysis?.status === 'ready' && p.analysis.points?.length === 3;
    const chosenSide = (p.side === 'left' || p.side === 'right' ? p.side : p.analysis?.side || (profileSide === 'unspecified' ? '' : profileSide));
    geometryDirty.current = saved;
    analysisSignature.current = (p.analysis?.id || '') + ':' + p.analysis?.status;
    setSelected(p); setPoints(saved ? p.points! : proposal ? p.analysis!.points! : guideArms()); setAligned(saved || !!proposal);
    setOrigin(!saved && proposal ? p.analysis?.id : undefined); setCaption(p.caption); setPhotoDate(p.date); setSide(chosenSide);
    setMode(p.measurement_type || 'unclassified'); setDirection(p.extension_direction || ''); setSizeReady(false); setPreview(undefined); setReviewed(false); setError(''); setDeleting(false);
    if (!p.analysis || ['needs_side','disabled'].includes(p.analysis.status)) enqueue(p, chosenSide);
  }
  function startUpload() { setUpload(true); setCaption(''); setPhotoDate(date); setSide(profileSide === 'unspecified' ? '' : profileSide); setMode('unclassified'); setFile(undefined); setError(''); }
  async function submit(e: FormEvent) {
    e.preventDefault(); if (!file) return; setBusy(true); setError('');
    try { const form = new FormData(); form.append('file',file); form.append('caption',caption); form.append('photo_date',photoDate); form.append('measurement_type',mode); if (side) form.append('side',side); await api('/photos','POST',form); await refresh(); setUpload(false); setFile(undefined); notify('Photo saved. Automatic analysis will prepare an alignment for review.'); }
    catch(e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function batchAnalyze() {
    setBatchBusy(true); setError('');
    try { const result = await api<{ queued: string[]; skipped: string[]; needs_side: string[] }>('/photos/analyze-batch','POST',{ ...(batchSide ? { side: batchSide } : {}), retry_failed: true }); setBatchSummary(`${result.queued.length} queued · ${result.skipped.length} already ready, queued or reviewed${result.needs_side.length ? ` · ${result.needs_side.length} need a leg selected` : ''}. No daily measurements were changed.`); await refresh(); }
    catch(e) { setError((e as Error).message); } finally { setBatchBusy(false); }
  }
  async function detect() {
    if (!selected || !side) return; setDetecting(true); setError(''); geometryDirty.current = false; invalidate();
    try { const result = await api<Photo>('/photos/'+selected.id+'/analyze','POST',{ side }); if (activeId.current === result.id && result.analysis?.status === 'ready' && result.analysis.points && !geometryDirty.current) { setPoints(result.analysis.points); setAligned(true); setOrigin(result.analysis.id); } await refresh(); }
    catch(e) { await refresh().catch(() => {}); if (activeId.current === selected.id) setError((e as Error).message); }
    finally { setDetecting(false); }
  }
  function useProposal() { if (!selected?.analysis?.points) return; geometryDirty.current = false; setPoints(selected.analysis.points); setAligned(true); setOrigin(selected.analysis.id); invalidate(); }
  const angle = aligned && sizeReady ? kneeBend(points,size.w,size.h) : null;
  const value = signedValue(angle,mode,direction);
  const valid = value != null && (mode !== 'extension' || value >= -30 && value <= 90);
  const waiting = detecting || ['queued','running'].includes(selected?.analysis?.status || '');
  const lastMeasurement = selected?.measurements?.at(-1);
  const previousValue = preview && mode !== 'unclassified' ? preview.existing?.[mode] : null;

  async function savePhoto() {
    if (!selected) return; setBusy(true); setError('');
    try { await api('/photos/'+selected.id,'PUT',{ date: photoDate, caption, points: aligned ? points : [], measurement_type: mode, extension_direction: direction || null, ...(side ? { side } : {}) }); await refresh(); closeAfterSave(); notify('Photo alignment saved. Daily measurements are unchanged.'); }
    catch(e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  function closeAfterSave() { activeId.current = undefined; setSelected(undefined); setReviewBatch(false); }
  async function commitMeasurement(proposal: Preview) {
    if (!selected || !side || mode === 'unclassified') return;
    await api('/photos/'+selected.id+'/apply','POST',{ date: photoDate, caption, points, side, reviewed: true, metric: mode, extension_direction: mode === 'extension' ? direction : null, expected_version: proposal.version, analysis_id: origin || null },{ 'Idempotency-Key': proposal.key });
    const fresh = await refresh(); await entrySaved();
    visited.current.add(selected.id);
    const next = reviewBatch ? fresh.find(p => !visited.current.has(p.id) && !p.measurements?.length) : undefined;
    if (next) open(next); else closeAfterSave();
    notify(`Reviewed ${modeLabel(mode)} estimate saved to your daily entry.`);
  }
  async function saveMeasurement() {
    if (!selected || !valid || !reviewed || mode === 'unclassified') return; setBusy(true); setError('');
    try { if (preview) { await commitMeasurement(preview); } else { const result = await api<EntryPreview>('/entries/preview','POST',{ date: photoDate, values: { [mode]: value } }); const proposal = { ...result, key: newKey() }; if (result.existing?.[mode] != null && result.existing[mode] !== value) setPreview(proposal); else await commitMeasurement(proposal); } }
    catch(e) { setError((e as Error).message); setPreview(undefined); } finally { setBusy(false); }
  }
  async function remove() { if (!selected) return; setBusy(true); try { await api('/photos/'+selected.id,'DELETE'); await refresh(); closeAfterSave(); notify('Photo deleted.'); } catch(e) { setError((e as Error).message); } finally { setBusy(false); } }
  function nextPhoto(delta: number) { if (!selected) return; const index = photos.findIndex(p => p.id === selected.id); const next = photos[index+delta]; if (next) open(next); }
  function reviewPhotos() { visited.current.clear(); setReviewBatch(true); const first = photos.find(p => !p.measurements?.length) || photos[0]; if (first) open(first); }
  function skipPhoto() { if (!selected) return; visited.current.add(selected.id); const next = photos.find(p => !visited.current.has(p.id) && !p.measurements?.length); if (next) open(next); else close(); }
  const modeControls = (forUpload = false) => <label className="field">Measurement type<select aria-label={forUpload ? 'Upload measurement type' : 'Measurement type'} value={mode} disabled={busy} onChange={e => { setMode(e.target.value as PhotoMode); invalidate(); }}><option value="unclassified">Choose type / progress photo only</option><option value="flexion">Flexion · bending</option><option value="extension">Extension · straightening</option></select></label>;
  const sideControl = (forUpload = false) => <label className="field">Leg in photo<select aria-label={forUpload ? 'Upload leg in photo' : 'Leg in photo'} value={side} disabled={busy} onChange={e => { const next = e.target.value as typeof side; setSide(next); setOrigin(undefined); invalidate(); if (!forUpload && selected && next) enqueue(selected,next); }}><option value="">Choose a leg</option><option value="left">Left</option><option value="right">Right</option></select></label>;

  return <>
    <div className="section-toolbar"><p>Photos become proposals. You decide what enters your journal.</p><div className="button-row"><button className="button secondary" onClick={() => refresh().catch(e => setError(e.message))}><RefreshCw size={15}/> Refresh</button><button className="button primary" onClick={startUpload}><ImagePlus size={17}/> Add photo</button></div></div>
    <div className="info-strip"><Ruler size={18}/><p>Choose Flexion or Extension before logging. Automatic detection works best with the hip, knee and ankle visible. For cropped leg photos, align the thigh and shin arms yourself; the hip does not need to be in frame for manual alignment.</p></div>
    {photos.length > 0 && <section className="photo-batch-bar"><div><strong>{pending ? `${pending} photo${pending === 1 ? '' : 's'} being analysed` : 'Review your existing photos'}</strong><p>{batchSummary || 'New and unprocessed photos are analysed automatically when a leg is known. Saved measurements stay unchanged.'}</p></div><div className="button-row"><select aria-label="Batch analysis leg" value={batchSide} onChange={e => setBatchSide(e.target.value)}><option value="">Use each photo’s saved leg</option><option value="left">Left leg</option><option value="right">Right leg</option></select><button className="button secondary small" disabled={batchBusy} onClick={batchAnalyze}><Sparkles size={15}/>{batchBusy ? 'Queuing…' : 'Analyse all photos'}</button><button className="button secondary small" onClick={reviewPhotos}>Review photos <ChevronRight size={15}/></button></div></section>}
    {status && !status.pose.available && <p className="field-help">Local detection is offline. Photos remain queued until it is available; manual alignment works now.</p>}
    {error && !upload && !selected && <p className="error" role="alert">{error}</p>}
    {photos.length ? <div className="photo-grid">{photos.map(p => {
      const typed = p.measurement_type && p.measurement_type !== 'unclassified';
      return <button className="photo-card" key={p.id} onClick={() => { setReviewBatch(false); open(p); }}><img src={'/api/photos/'+p.id+'/image'} alt={p.caption || 'Rehab photo from '+dateLabel(p.date,true)} loading="lazy"/><div><span>{dateLabel(p.date,true)}</span>{p.angle != null && <span className="pill">{typed && p.measurement_value != null ? `${fmt(p.measurement_value)}° ${p.measurement_type}` : `${fmt(p.angle)}° alignment · choose measurement type`}</span>}{p.source === 'telegram' && <span className="pill">From Telegram</span>}<span className={'pill analysis-badge '+(p.analysis?.status || '')}>{p.analysis?.status === 'ready' ? 'Alignment ready to review' : p.analysis?.status === 'queued' ? 'Queued for analysis' : p.analysis?.status === 'running' ? 'Analysing…' : p.analysis?.status === 'failed' ? 'Manual alignment needed' : p.analysis?.status === 'needs_side' ? 'Select leg to analyse' : 'Manual alignment available'}</span>{p.measurements?.length ? <span className="pill">Previously reviewed & logged</span> : null}<p>{p.caption || 'Open to align and review →'}</p></div></button>;
    })}</div> : <div className="panel photos-empty"><span className="empty-icon"><Camera size={28} strokeWidth={1.3}/></span><h2>Your recovery, frame by frame.</h2><p>Upload a photo for automatic analysis, then review the alignment.<br/>Flexion and extension are logged separately.</p><button className="button primary" onClick={startUpload}><Upload size={16}/> Upload your first photo</button><small>JPEG, PNG or WebP · up to 12 MB</small></div>}

    {upload && <Modal title="Add a progress photo" close={() => !busy && setUpload(false)}><form onSubmit={submit}><div className="upload-zone"><ImagePlus size={30}/><strong>{file?.name || 'Choose a photo from your device'}</strong><span>JPEG, PNG or WebP · up to 12 MB</span><input type="file" accept="image/jpeg,image/png,image/webp" required onChange={e => { const f = e.target.files?.[0]; if (f && f.size > 12*1024*1024) { setError('Choose a photo smaller than 12 MB.'); e.target.value = ''; setFile(undefined); } else { setFile(f); setError(''); } }}/></div><div className="field-grid">{modeControls(true)}{sideControl(true)}</div><label className="field">Photo date<input required type="date" value={photoDate} onChange={e => setPhotoDate(e.target.value)}/></label><label className="field">Note<textarea rows={3} value={caption} maxLength={1000} onChange={e => setCaption(e.target.value)} placeholder="Straightening check, before exercises…"/></label><p className="field-help">Analysis starts automatically when a leg is selected. It creates a reviewable proposal, never a daily measurement.</p>{error && <p className="error" role="alert">{error}</p>}<div className="modal-footer"><span>Stored privately on your server</span><button className="button primary" disabled={busy || !file}>{busy ? 'Uploading…' : 'Save photo'}<Upload size={16}/></button></div></form></Modal>}

    {selected && <Modal title="Align. Review. Save." subtitle="Two arms follow the thigh and shin. No off-screen hip point needed." close={close} wide>
      <div className="photo-review-navigation"><span>Photo {photos.findIndex(p => p.id === selected.id)+1} of {photos.length}</span><div className="button-row"><button className="icon-button" aria-label="Previous photo" disabled={busy || photos[0]?.id === selected.id} onClick={() => nextPhoto(-1)}><ChevronLeft size={18}/></button><button className="icon-button" aria-label="Next photo" disabled={busy || photos.at(-1)?.id === selected.id} onClick={() => nextPhoto(1)}><ChevronRight size={18}/></button>{reviewBatch && <button className="text-button" disabled={busy} onClick={skipPhoto}>Skip photo</button>}</div></div>
      <div className="field-grid">{modeControls()}{sideControl()}</div>
      {mode === 'extension' && <fieldset className="extension-direction"><legend>Direction of this extension measurement</legend><label><input type="radio" name="extension-direction" value="deficit" checked={direction === 'deficit'} disabled={busy} onChange={() => { setDirection('deficit'); invalidate(); }}/><span><strong>Short of straight</strong><small>Positive extension deficit (+)</small></span></label><label><input type="radio" name="extension-direction" value="hyperextension" checked={direction === 'hyperextension'} disabled={busy} onChange={() => { setDirection('hyperextension'); invalidate(); }}/><span><strong>Beyond straight</strong><small>Hyperextension, negative (−)</small></span></label><p>The photo angle gives the magnitude. Your direction choice determines the sign; it is not inferred automatically.</p></fieldset>}
      <div className={'detection-notice '+(selected.analysis?.status || '')}><div><strong>{waiting ? 'Preparing an automatic alignment…' : selected.analysis?.status === 'ready' ? 'Automatic alignment available' : selected.analysis?.status === 'failed' ? 'Use manual axis alignment for this photo' : side ? 'Align the arms, or try automatic detection' : 'Select a leg for automatic analysis'}</strong><p>{selected.analysis?.status === 'failed' ? selected.analysis.detail : selected.analysis?.status === 'ready' ? `Local model: ${selected.analysis.model}. Verify the selected leg and both arm directions. Visibility scores do not establish angle accuracy.` : 'You can adjust the guides while analysis runs. Automatic results will not replace an alignment you have edited.'}</p></div><div className="button-row">{selected.analysis?.status === 'ready' && <button className="button secondary small" disabled={busy} onClick={useProposal}>Use detected alignment</button>}<button className="button secondary small" disabled={busy || waiting || !side} onClick={detect}><Sparkles size={14}/>{waiting ? 'Analysing…' : 'Detect landmarks'}</button></div></div>
      <AngleTool key={selected.id} url={'/api/photos/'+selected.id+'/image'} points={points} aligned={aligned} disabled={busy} onChange={changePoints} onReset={resetGuides} onSize={next => { setSize(next); setSizeReady(true); }}/>
      <div className="angle-summary"><div><strong>{value != null ? (mode === 'extension' && value > 0 ? '+' : '')+fmt(value)+'°' : angle != null ? fmt(angle)+'°' : 'Align the arms'}</strong><span>{mode === 'unclassified' ? 'Select Flexion or Extension before logging' : mode === 'extension' ? direction ? direction === 'hyperextension' ? 'Extension · hyperextension (negative)' : 'Extension · short of straight (positive)' : 'Choose the extension direction to set the sign' : 'Estimated flexion · 0° = straight'}</span></div><span className="subtle-tag">{origin ? 'Model-assisted alignment' : aligned ? 'Manual alignment' : 'Guides only'}</span></div>
      {mode === 'extension' && value != null && !valid && <p className="error" role="alert">This alignment is outside the supported extension range (−30° to +90°). Check the arms and measurement type.</p>}
      <div className="field-grid"><label className="field">Photo date<input required disabled={busy} type="date" value={photoDate} onChange={e => { setPhotoDate(e.target.value); invalidate(); }}/></label><label className="field">Caption<input disabled={busy} value={caption} maxLength={1000} onChange={e => { setCaption(e.target.value); invalidate(); }}/></label></div>
      {lastMeasurement && <p className="legacy-measurement">Previously logged: {fmt(lastMeasurement.value)}° {lastMeasurement.metric || 'flexion (legacy log)'} on {dateLabel(lastMeasurement.date,true)}. Changing the photo type does not move or delete that measurement.</p>}
      <div className="measurement-review"><label className="checkbox-label"><input type="checkbox" checked={reviewed} disabled={busy || !valid || !side || !aligned} onChange={e => { setReviewed(e.target.checked); setPreview(undefined); }}/> I reviewed the measurement type, leg, alignment and {mode === 'extension' ? 'extension direction' : 'angle'}.</label>{preview ? <div className="measurement-preview" role="status"><strong>Replace {fmt(previousValue)}° with {fmt(value)}° {modeLabel(mode)}?</strong><p>{dateLabel(photoDate,true)} · This replaces only the existing {modeLabel(mode)} value. Your other measurements and notes are kept.</p><div className="button-row"><button className="button primary" disabled={busy || !reviewed} onClick={saveMeasurement}>Confirm replacement</button><button className="text-button" disabled={busy} onClick={() => setPreview(undefined)}>Cancel</button></div></div> : <button className="button primary" disabled={busy || !reviewed || !valid || !side || !photoDate} onClick={saveMeasurement}><Save size={15}/>{busy ? 'Saving…' : `Save ${mode === 'unclassified' ? 'reviewed' : modeLabel(mode)} measurement${reviewBatch ? ' & next' : ''}`}</button>}</div>
      <p className="field-help">These are estimates from a 2D photo, not a validated clinical measurement. Camera position, visible anatomy and alignment affect the result. Use a consistent setup and check the technique with your physio.</p>
      {error && <p className="error" role="alert">{error}</p>}<div className="modal-footer">{deleting ? <span className="delete-confirm">Delete permanently?<button className="text-button danger-text" disabled={busy} onClick={remove}>Yes, delete</button><button className="text-button" onClick={() => setDeleting(false)}>Cancel</button></span> : <button className="text-button danger-text" disabled={busy} onClick={() => setDeleting(true)}><Trash2 size={15}/> Delete photo</button>}<button className="button secondary" disabled={busy || !photoDate} onClick={savePhoto}>Save alignment only</button></div>
    </Modal>}
  </>;
}
