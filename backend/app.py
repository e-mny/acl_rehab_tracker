"""Stride: one household, one rehab journal, local durable storage."""
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import threading
import time
import tempfile
import warnings
import zipfile
import math
import asyncio
import logging
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, model_validator

DATA = Path(os.environ.get('DATA_DIR', './data')).resolve()
DIST = Path(os.environ.get('STATIC_DIR', 'frontend/dist')).resolve()
COOKIE = 'stride_session'
MAX_IMAGE = 12 * 1024 * 1024
lock = threading.Lock()
attempts: dict[str, list[float]] = {}


@contextmanager
def db():
    connection = sqlite3.connect(DATA / 'stride.sqlite3', timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def initialize():
    (DATA / 'photos').mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (hash TEXT PRIMARY KEY, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, date TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS photos (id TEXT PRIMARY KEY, date TEXT NOT NULL, caption TEXT NOT NULL,
            filename TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS operations (key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS photo_jobs (photo_id TEXT PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
            side TEXT NOT NULL, analysis_id TEXT NOT NULL, state TEXT NOT NULL, available_at REAL NOT NULL DEFAULT 0);
        ''')
        c.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('profile', json.dumps(Profile().model_dump(mode='json'))))
        seed = Path(__file__).with_name('seed_history.json')
        if seed.exists() and not c.execute("SELECT 1 FROM settings WHERE key='history_import_v1'").fetchone():
            history = json.loads(seed.read_text())
            for record in history:
                entry = Entry(**record)
                c.execute('INSERT OR IGNORE INTO entries VALUES (?,?,?)',
                          ('history-' + str(entry.date), str(entry.date), entry.model_dump_json()))
            set_setting(c, 'history_import_v1', 'done')
        c.execute("UPDATE photo_jobs SET state='queued' WHERE state='running'")
        profile = json.loads(c.execute("SELECT value FROM settings WHERE key='profile'").fetchone()['value'])
        queue_unprocessed(c, profile.get('side'))


@asynccontextmanager
async def lifespan(app):
    initialize()
    worker = asyncio.create_task(photo_analysis_loop())
    try:
        yield
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker


app = FastAPI(title='Stride Rehab API', version='1.0.0', lifespan=lifespan,
              docs_url=None, openapi_url='/api/openapi.json', redoc_url=None)


def setting(key: str):
    with db() as c:
        row = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row['value'] if row else None


def set_setting(c, key, value):
    c.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def authorized(request: Request):
    authorization = request.headers.get('authorization', '')
    if authorization.startswith('Bearer '):
        expected = setting('api_token_hash')
        if expected and hmac.compare_digest(digest(authorization[7:]), expected):
            return 'token'
    token = request.cookies.get(COOKIE)
    if token:
        with db() as c:
            session = c.execute('SELECT expires FROM sessions WHERE hash=?', (digest(token),)).fetchone()
            if session and session['expires'] > time.time():
                return 'session'
    raise HTTPException(401, 'Please sign in to your journal.')


def browser_session(auth=Depends(authorized)):
    if auth != 'session':
        raise HTTPException(403, 'Use a browser session to manage security settings.')


@app.middleware('http')
async def safety_headers(request: Request, call_next):
    # A custom header prevents cross-origin browser forms from mutating this LAN service.
    if request.url.path.startswith('/api/') and request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if request.headers.get('x-stride-request') != '1' and not request.headers.get('authorization', '').startswith('Bearer '):
            return Response('Missing X-Stride-Request header', status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Credentials(StrictModel):
    password: str = Field(min_length=10, max_length=256)


class Profile(StrictModel):
    name: str = Field(default='', max_length=80)
    start_date: date | None = None
    side: Literal['left', 'right', 'unspecified'] = 'unspecified'
    context: str = Field(default='', max_length=2000)
    timezone: str = Field(default='Asia/Singapore', max_length=80)


Cm = Annotated[float | None, Field(ge=5, le=150)]
Score = Annotated[float | None, Field(ge=0, le=10)]


class Entry(StrictModel):
    date: date
    knee: Cm = None
    above5: Cm = None
    above15: Cm = None
    other_knee: Cm = None
    other_above5: Cm = None
    other_above15: Cm = None
    flexion: Annotated[float | None, Field(ge=0, le=180)] = None
    extension: Annotated[float | None, Field(ge=-30, le=90)] = None
    pain_rest: Score = None
    pain_activity: Score = None
    stiffness: Annotated[int | None, Field(ge=0, le=1440)] = None
    exercise: Annotated[int | None, Field(ge=0, le=1440)] = None
    steps: Annotated[int | None, Field(ge=0, le=100000)] = None
    sleep: Annotated[float | None, Field(ge=0, le=24)] = None
    instability: Annotated[int | None, Field(ge=0, le=100)] = None
    notes: str = Field(default='', max_length=5000)
    source: Literal['manual', 'telegram', 'api'] = 'manual'

    @model_validator(mode='after')
    def has_content(self):
        values = self.model_dump(exclude={'date', 'notes', 'source'})
        if not any(value is not None for value in values.values()) and not self.notes.strip():
            raise ValueError('Add at least one measurement or a note.')
        if self.date > date.today():
            # Up to a day permits clients ahead of the server timezone.
            from datetime import timedelta
            if self.date > date.today() + timedelta(days=1):
                raise ValueError('Entries cannot be in the future.')
        return self


class Landmark(StrictModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


# Reuse Entry's validation constraints while distinguishing omitted fields from explicit nulls.
EntryPatch = create_model('EntryPatch', __base__=StrictModel, **{
    name: (Annotated[field.annotation, *field.metadata] if field.metadata else field.annotation, None)
    for name, field in Entry.model_fields.items() if name != 'date'
})


class EntryDraft(StrictModel):
    date: date
    values: EntryPatch


class AnalyzePhoto(StrictModel):
    side: Literal['left', 'right']


class PoseResult(StrictModel):
    points: list[Landmark] = Field(min_length=3, max_length=3)
    confidence: float = Field(ge=0, le=1)
    model: str = Field(max_length=150)
    model_version: str = Field(max_length=100)


class ApplyPhoto(StrictModel):
    date: date
    caption: str = Field(default='', max_length=1000)
    points: list[Landmark] = Field(min_length=3, max_length=3)
    side: Literal['left', 'right']
    reviewed: Literal[True]
    expected_version: str = Field(max_length=100)
    metric: Literal['flexion', 'extension'] = 'flexion'
    extension_direction: Literal['deficit', 'hyperextension'] | None = None
    analysis_id: str | None = Field(default=None, max_length=100)

    @model_validator(mode='after')
    def direction_required(self):
        if self.metric == 'extension' and self.extension_direction is None:
            raise ValueError('Choose short of straight or hyperextension for this extension photo.')
        return self


class BatchAnalysis(StrictModel):
    side: Literal['left', 'right'] | None = None
    photo_ids: list[str] | None = Field(default=None, max_length=500)
    retry_failed: bool = True


class BotHeartbeat(StrictModel):
    username: str = Field(max_length=100)


class PhotoUpdate(StrictModel):
    date: date
    caption: str = Field(default='', max_length=1000)
    points: list[Landmark] = Field(default_factory=list, max_length=3)
    angle: float | None = Field(default=None, ge=0, le=180)
    measurement_type: Literal['flexion', 'extension', 'unclassified'] = 'unclassified'
    extension_direction: Literal['deficit', 'hyperextension'] | None = None
    side: Literal['left', 'right'] | None = None


def entry_version(row):
    return digest(row['data']) if row else 'new'


def validated_entry(data):
    try:
        return Entry(**data)
    except ValidationError as e:
        raise HTTPException(422, e.errors()[0]['msg'])


def merge_entry(c, day, values, expected_version=None, write=False):
    row = c.execute('SELECT * FROM entries WHERE date=?', (str(day),)).fetchone()
    version = entry_version(row)
    if expected_version is not None and expected_version != version:
        raise HTTPException(409, 'This entry changed since your preview. Review the latest values and confirm again.')
    previous = json.loads(row['data']) if row else {}
    entry = validated_entry({**previous, **values, 'date': str(day)})
    identifier = row['id'] if row else secrets.token_hex(12)
    if write:
        c.execute('INSERT INTO entries VALUES (?,?,?) ON CONFLICT(date) DO UPDATE SET data=excluded.data',
                  (identifier, str(day), entry.model_dump_json()))
    return {'entry': dict(entry.model_dump(mode='json'), id=identifier), 'version': version,
            'existing': dict(previous, id=identifier) if row else None}


def cached_operation(c, key, fingerprint):
    if not key:
        return None
    if len(key) > 200:
        raise HTTPException(422, 'Idempotency key is too long.')
    row = c.execute('SELECT * FROM operations WHERE key=?', (key,)).fetchone()
    if row:
        if row['fingerprint'] != fingerprint:
            raise HTTPException(409, 'This idempotency key was already used for a different request.')
        return json.loads(row['result'])


def cache_operation(c, key, fingerprint, result):
    if key:
        c.execute('INSERT INTO operations VALUES (?,?,?)', (key, fingerprint, json.dumps(result)))


def bend_angle(points, path):
    if len(points) != 3:
        return None
    with Image.open(path) as image:
        width, height = image.size
    a, b, c = points
    u = ((a.x - b.x) * width, (a.y - b.y) * height)
    v = ((c.x - b.x) * width, (c.y - b.y) * height)
    length = math.hypot(*u) * math.hypot(*v)
    if length < 1:
        raise HTTPException(422, 'Landmarks must be distinct. Separate the hip, knee and ankle points.')
    return round(180 - math.degrees(math.acos(max(-1, min(1, (u[0]*v[0] + u[1]*v[1]) / length)))), 1)


def measurement_value(angle, metric, direction=None):
    if angle is None or metric == 'unclassified':
        return None
    if metric == 'extension':
        if direction not in ('deficit', 'hyperextension'):
            return None
        value = -angle if direction == 'hyperextension' else angle
        if not -30 <= value <= 90:
            raise HTTPException(422, 'This alignment is outside the supported extension range. Check the arms and measurement type.')
        return 0.0 if value == 0 else value
    return angle


def photo_record(row):
    return dict(id=row['id'], date=row['date'], caption=row['caption'], **json.loads(row['data']))


def enqueue_photo(c, identifier, side, retry=False):
    row = c.execute('SELECT * FROM photos WHERE id=?', (identifier,)).fetchone()
    if not row:
        raise HTTPException(404, 'Photo not found.')
    data = json.loads(row['data'])
    current = data.get('analysis', {})
    job = c.execute('SELECT state FROM photo_jobs WHERE photo_id=?', (identifier,)).fetchone()
    if job and job['state'] in ('queued', 'running') and current.get('side') == side:
        if current.get('status') != job['state']:
            data['analysis']['status'] = job['state']
            c.execute('UPDATE photos SET data=? WHERE id=?', (json.dumps(data), identifier))
            return dict(id=row['id'], date=row['date'], caption=row['caption'], **data), False
        return photo_record(row), False
    if current.get('status') in ('ready', 'failed') and not retry and current.get('side') == side:
        return photo_record(row), False
    if side not in ('left', 'right'):
        data['analysis'] = {'status': 'needs_side', 'detail': 'Choose the leg to start automatic analysis.'}
    elif not os.environ.get('POSE_ANALYZER_URL'):
        data['analysis'] = {'status': 'disabled', 'side': side, 'detail': 'Local analysis is offline. Manual alignment is available.'}
    else:
        analysis_id = secrets.token_hex(12)
        data['analysis'] = {'id': analysis_id, 'status': 'queued', 'side': side, 'created_at': time.time()}
        c.execute("INSERT INTO photo_jobs VALUES (?,?,?,'queued',0) ON CONFLICT(photo_id) DO UPDATE SET side=excluded.side,analysis_id=excluded.analysis_id,state='queued',available_at=0",
                  (identifier, side, analysis_id))
    c.execute('UPDATE photos SET data=? WHERE id=?', (json.dumps(data), identifier))
    return dict(id=row['id'], date=row['date'], caption=row['caption'], **data), data['analysis']['status'] == 'queued'


def queue_unprocessed(c, side):
    for row in c.execute('SELECT * FROM photos').fetchall():
        data = json.loads(row['data'])
        status = data.get('analysis', {}).get('status')
        if status not in ('ready', 'failed'):
            chosen = next((s for s in (data.get('side'), data.get('analysis', {}).get('side'), side) if s in ('left', 'right')), None)
            enqueue_photo(c, row['id'], chosen)


def session_cookie(response):
    token = secrets.token_urlsafe(32)
    with db() as c:
        c.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
        c.execute('INSERT INTO sessions VALUES (?,?)', (digest(token), time.time() + 30 * 86400))
    response.set_cookie(COOKIE, token, max_age=30 * 86400, httponly=True, samesite='strict',
                        secure=os.environ.get('COOKIE_SECURE', 'false').lower() == 'true')


@app.get('/api/health')
def health():
    with db() as c:
        c.execute('SELECT 1')
    return {'status': 'ok'}


@app.get('/api/docs', response_class=HTMLResponse, include_in_schema=False, dependencies=[Depends(browser_session)])
def api_docs():
    return Path(__file__).with_name('api_docs.html').read_text()


@app.get('/api/auth/status')
def auth_status(request: Request):
    try:
        authorized(request)
        authenticated = True
    except HTTPException:
        authenticated = False
    return {'initialized': setting('password') is not None, 'authenticated': authenticated}


@app.post('/api/auth/setup', status_code=201)
def setup(credentials: Credentials, response: Response):
    with lock, db() as c:
        if c.execute("SELECT 1 FROM settings WHERE key='password'").fetchone():
            raise HTTPException(409, 'This journal is already set up.')
        salt = secrets.token_hex(16)
        set_setting(c, 'password', json.dumps({'salt': salt, 'hash': password_hash(credentials.password, salt)}))
    session_cookie(response)
    return {'ok': True}


@app.post('/api/auth/login')
def login(credentials: Credentials, response: Response, request: Request):
    host = request.client.host if request.client else 'unknown'
    with lock:
        now = time.time()
        attempts[host] = [t for t in attempts.get(host, []) if t > now - 300]
        if len(attempts[host]) >= 10:
            raise HTTPException(429, 'Too many attempts. Try again in five minutes.')
        attempts[host].append(now)
    stored = setting('password')
    if not stored:
        raise HTTPException(409, 'Set up your journal first.')
    stored = json.loads(stored)
    if not hmac.compare_digest(password_hash(credentials.password, stored['salt']), stored['hash']):
        raise HTTPException(401, 'That password does not match.')
    with lock:
        attempts.pop(host, None)
    session_cookie(response)
    return {'ok': True}


@app.post('/api/auth/logout', dependencies=[Depends(authorized)])
def logout(request: Request, response: Response):
    with db() as c:
        c.execute('DELETE FROM sessions WHERE hash=?', (digest(request.cookies.get(COOKIE, '')),))
    response.delete_cookie(COOKIE)
    return {'ok': True}


@app.put('/api/auth/password', dependencies=[Depends(browser_session)])
def change_password(credentials: Credentials, response: Response):
    salt = secrets.token_hex(16)
    with db() as c:
        set_setting(c, 'password', json.dumps({'salt': salt, 'hash': password_hash(credentials.password, salt)}))
        c.execute('DELETE FROM sessions')
    session_cookie(response)
    return {'ok': True}


@app.get('/api/profile', dependencies=[Depends(authorized)])
def get_profile():
    return json.loads(setting('profile'))


@app.put('/api/profile', dependencies=[Depends(authorized)])
def put_profile(profile: Profile):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        ZoneInfo(profile.timezone)
    except ZoneInfoNotFoundError:
        raise HTTPException(422, 'Choose a valid IANA time zone, for example Asia/Singapore.')
    with db() as c:
        set_setting(c, 'profile', profile.model_dump_json())
        queue_unprocessed(c, profile.side)
    return profile


@app.get('/api/entries', dependencies=[Depends(authorized)])
def list_entries():
    with db() as c:
        return [dict(json.loads(r['data']), id=r['id']) for r in c.execute('SELECT * FROM entries ORDER BY date ASC')]


@app.post('/api/entries', status_code=201, dependencies=[Depends(authorized)])
def create_entry(entry: Entry):
    identifier = secrets.token_hex(12)
    try:
        with db() as c:
            c.execute('INSERT INTO entries VALUES (?,?,?)', (identifier, str(entry.date), entry.model_dump_json()))
    except sqlite3.IntegrityError:
        raise HTTPException(409, 'There is already an entry for this date. Edit that entry to add more measurements.')
    return dict(entry.model_dump(mode='json'), id=identifier)


@app.post('/api/entries/preview', dependencies=[Depends(authorized)])
def preview_entry(draft: EntryDraft):
    values = draft.values.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(422, 'Add at least one field.')
    with db() as c:
        return merge_entry(c, draft.date, values)


@app.patch('/api/entries/by-date/{entry_date}', dependencies=[Depends(authorized)])
def patch_entry(entry_date: date, patch: EntryPatch, if_match: str | None = Header(default=None),
                idempotency_key: str | None = Header(default=None)):
    values = patch.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(422, 'Add at least one field.')
    fingerprint = digest(json.dumps(['entry', str(entry_date), values], sort_keys=True))
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        cached = cached_operation(c, idempotency_key, fingerprint)
        if cached:
            return cached
        result = merge_entry(c, entry_date, values, expected_version=if_match, write=True)['entry']
        cache_operation(c, idempotency_key, fingerprint, result)
    return result


@app.put('/api/entries/{identifier}', dependencies=[Depends(authorized)])
def update_entry(identifier: str, entry: Entry):
    try:
        with db() as c:
            result = c.execute('UPDATE entries SET date=?,data=? WHERE id=?', (str(entry.date), entry.model_dump_json(), identifier))
            if not result.rowcount:
                raise HTTPException(404, 'Entry not found.')
    except sqlite3.IntegrityError:
        raise HTTPException(409, 'There is already an entry for this date.')
    return dict(entry.model_dump(mode='json'), id=identifier)


@app.delete('/api/entries/{identifier}', status_code=204, dependencies=[Depends(authorized)])
def delete_entry(identifier: str):
    with db() as c:
        if not c.execute('DELETE FROM entries WHERE id=?', (identifier,)).rowcount:
            raise HTTPException(404, 'Entry not found.')


@app.get('/api/photos', dependencies=[Depends(authorized)])
def list_photos():
    with db() as c:
        return [dict(id=r['id'], date=r['date'], caption=r['caption'], **json.loads(r['data']))
                for r in c.execute('SELECT * FROM photos ORDER BY date DESC, rowid DESC')]


@app.post('/api/photos', status_code=201, dependencies=[Depends(authorized)])
async def upload_photo(file: UploadFile = File(...), photo_date: date = Form(...), caption: str = Form(''),
                       source: Literal['manual', 'telegram', 'api'] = Form('manual'),
                       side: Literal['left', 'right'] | None = Form(None),
                       measurement_type: Literal['flexion', 'extension', 'unclassified'] = Form('unclassified'),
                       idempotency_key: str | None = Header(default=None)):
    if len(caption) > 1000:
        raise HTTPException(422, 'Caption must be under 1,000 characters.')
    content = await file.read(MAX_IMAGE + 1)
    await file.close()
    if len(content) > MAX_IMAGE:
        raise HTTPException(413, 'Choose an image smaller than 12 MB.')
    fingerprint = digest(json.dumps(['photo', str(photo_date), caption, source, side, measurement_type, hashlib.sha256(content).hexdigest()]))
    with db() as c:
        cached = cached_operation(c, idempotency_key, fingerprint)
        if cached:
            return cached
    identifier = secrets.token_hex(12)
    filename = identifier + '.jpg'
    target = DATA / 'photos' / filename
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format not in ('JPEG', 'PNG', 'WEBP'):
                    raise HTTPException(415, 'Use a JPEG, PNG or WebP image.')
                # Normalize orientation and discard EXIF (including GPS) on upload.
                image = ImageOps.exif_transpose(image).convert('RGB')
                image.thumbnail((2400, 2400))
                image.save(target, 'JPEG', quality=90)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning, ValueError):
        target.unlink(missing_ok=True)
        raise HTTPException(415, 'The image could not be read. Use a JPEG, PNG or WebP image.')
    try:
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            cached = cached_operation(c, idempotency_key, fingerprint)
            if cached:
                target.unlink(missing_ok=True)
                return cached
            profile = json.loads(c.execute("SELECT value FROM settings WHERE key='profile'").fetchone()['value'])
            chosen_side = side or profile.get('side')
            c.execute('INSERT INTO photos (id,date,caption,filename,data) VALUES (?,?,?,?,?)', (identifier, str(photo_date), caption, filename, json.dumps({'source': source, 'measurement_type': measurement_type, 'side': chosen_side})))
            result, _ = enqueue_photo(c, identifier, chosen_side)
            cache_operation(c, idempotency_key, fingerprint, result)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return result


@app.get('/api/photos/{identifier}/image', dependencies=[Depends(authorized)])
def photo_image(identifier: str):
    with db() as c:
        row = c.execute('SELECT filename FROM photos WHERE id=?', (identifier,)).fetchone()
    if not row:
        raise HTTPException(404, 'Photo not found.')
    return FileResponse(DATA / 'photos' / row['filename'], media_type='image/jpeg')


@app.put('/api/photos/{identifier}', dependencies=[Depends(authorized)])
def update_photo(identifier: str, photo: PhotoUpdate):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM photos WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise HTTPException(404, 'Photo not found.')
        data = json.loads(row['data'])
        # Angles are recomputed in image pixels; arbitrary client estimates are not trusted.
        angle = bend_angle(photo.points, DATA / 'photos' / row['filename'])
        data.update(points=[p.model_dump() for p in photo.points], angle=angle)
        if 'measurement_type' in photo.model_fields_set:
            data['measurement_type'] = photo.measurement_type
            data['extension_direction'] = photo.extension_direction
        if photo.side:
            data['side'] = photo.side
        data['measurement_value'] = measurement_value(angle, data.get('measurement_type', 'unclassified'), data.get('extension_direction'))
        result = c.execute('UPDATE photos SET date=?,caption=?,data=? WHERE id=?',
                           (str(photo.date), photo.caption, json.dumps(data), identifier))
        if not result.rowcount:
            raise HTTPException(404, 'Photo not found.')
    return dict(id=identifier, date=str(photo.date), caption=photo.caption, **data)


@app.get('/api/integrations', dependencies=[Depends(authorized)])
def integration_status():
    url = os.environ.get('POSE_ANALYZER_URL', '').rstrip('/')
    available = False
    if url:
        try:
            available = httpx.get(url + '/health', timeout=2).is_success
        except httpx.HTTPError:
            pass
    bot = json.loads(setting('telegram_heartbeat') or '{}')
    return {'pose': {'configured': bool(url), 'available': available},
            'telegram': {**bot, 'online': bool(bot and time.time() - bot.get('last_seen', 0) < 120)}}


@app.post('/api/integrations/telegram/heartbeat', dependencies=[Depends(authorized)])
def telegram_heartbeat(heartbeat: BotHeartbeat):
    with db() as c:
        set_setting(c, 'telegram_heartbeat', json.dumps({'username': heartbeat.username, 'last_seen': time.time()}))
    return {'ok': True}


async def perform_photo_analysis(identifier, side, analysis_id):
    url = os.environ.get('POSE_ANALYZER_URL', '').rstrip('/')
    with db() as c:
        row = c.execute('SELECT * FROM photos WHERE id=?', (identifier,)).fetchone()
    if not row:
        raise HTTPException(404, 'Photo not found.')
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            with (DATA / 'photos' / row['filename']).open('rb') as image:
                response = await client.post(url + '/analyze', files={'file': ('photo.jpg', image, 'image/jpeg')}, data={'side': side})
        if response.status_code == 422:
            detail = response.json().get('detail', '')
            detail = detail.get('message', '') if isinstance(detail, dict) else detail
            raise HTTPException(422, str(detail)[:1000] or 'Landmarks could not be identified. Align the thigh and shin arms manually.')
        response.raise_for_status()
        result = PoseResult(**response.json())
        analysis = {'id': analysis_id, 'status': 'ready', 'side': side, **result.model_dump(),
                    'angle': bend_angle(result.points, DATA / 'photos' / row['filename']), 'created_at': time.time()}
    except (httpx.HTTPError, OSError, ValidationError, ValueError, HTTPException) as e:
        detail = e.detail if isinstance(e, HTTPException) else 'Local analysis is unavailable. Align the arms manually, or retry when the service is online.'
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            fresh = c.execute('SELECT data FROM photos WHERE id=?', (identifier,)).fetchone()
            if fresh:
                data = json.loads(fresh['data'])
                if data.get('analysis', {}).get('id') == analysis_id:
                    data['analysis'] = {'id': analysis_id, 'status': 'failed', 'side': side, 'detail': detail}
                    c.execute('UPDATE photos SET data=? WHERE id=?', (json.dumps(data), identifier))
            c.execute("UPDATE photo_jobs SET state='failed' WHERE photo_id=? AND analysis_id=?", (identifier, analysis_id))
        raise HTTPException(e.status_code if isinstance(e, HTTPException) else 503, detail)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        fresh = c.execute('SELECT * FROM photos WHERE id=?', (identifier,)).fetchone()
        if not fresh:
            raise HTTPException(404, 'Photo was deleted during analysis.')
        data = json.loads(fresh['data'])
        if data.get('analysis', {}).get('id') != analysis_id:
            raise HTTPException(409, 'A newer analysis replaced this proposal.')
        data['analysis'] = analysis
        c.execute('UPDATE photos SET data=? WHERE id=?', (json.dumps(data), identifier))
        c.execute("UPDATE photo_jobs SET state='ready' WHERE photo_id=? AND analysis_id=?", (identifier, analysis_id))
    return dict(id=identifier, date=fresh['date'], caption=fresh['caption'], **data)


async def photo_analysis_loop():
    while True:
        try:
            with db() as c:
                pending = c.execute("SELECT 1 FROM photo_jobs WHERE state='queued' AND available_at<=?", (time.time(),)).fetchone()
            url = os.environ.get('POSE_ANALYZER_URL', '').rstrip('/')
            if not pending or not url:
                await asyncio.sleep(1)
                continue
            # Leave durable jobs queued while the optional worker is offline.
            try:
                async with httpx.AsyncClient(timeout=2) as client:
                    online = (await client.get(url + '/health')).is_success
            except httpx.HTTPError:
                online = False
            if not online:
                await asyncio.sleep(5)
                continue
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                job = c.execute("SELECT * FROM photo_jobs WHERE state='queued' AND available_at<=? ORDER BY rowid LIMIT 1", (time.time(),)).fetchone()
                if not job:
                    continue
                c.execute("UPDATE photo_jobs SET state='running' WHERE photo_id=?", (job['photo_id'],))
                row = c.execute('SELECT data FROM photos WHERE id=?', (job['photo_id'],)).fetchone()
                data = json.loads(row['data'])
                data['analysis']['status'] = 'running'
                c.execute('UPDATE photos SET data=? WHERE id=?', (json.dumps(data), job['photo_id']))
            try:
                await perform_photo_analysis(job['photo_id'], job['side'], job['analysis_id'])
            except HTTPException as e:
                if e.status_code == 503:
                    with db() as c:
                        c.execute('BEGIN IMMEDIATE')
                        row = c.execute('SELECT data FROM photos WHERE id=?', (job['photo_id'],)).fetchone()
                        if row:
                            data = json.loads(row['data'])
                            if data.get('analysis', {}).get('id') == job['analysis_id']:
                                data['analysis']['status'] = 'queued'
                                c.execute('UPDATE photos SET data=? WHERE id=?', (json.dumps(data), job['photo_id']))
                                c.execute("UPDATE photo_jobs SET state='queued',available_at=? WHERE photo_id=? AND analysis_id=?", (time.time()+30, job['photo_id'], job['analysis_id']))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.getLogger('stride').warning('Photo queue paused after %s; retrying.', type(e).__name__)
            await asyncio.sleep(5)


@app.post('/api/photos/{identifier}/queue', dependencies=[Depends(authorized)])
def queue_photo(identifier: str, options: AnalyzePhoto):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        return enqueue_photo(c, identifier, options.side)[0]


@app.post('/api/photos/{identifier}/analyze', dependencies=[Depends(authorized)])
async def analyze_photo(identifier: str, options: AnalyzePhoto):
    if not os.environ.get('POSE_ANALYZER_URL'):
        raise HTTPException(503, 'Local analysis is disabled. Manual alignment is available.')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        photo, _ = enqueue_photo(c, identifier, options.side, retry=True)
        job = c.execute('SELECT * FROM photo_jobs WHERE photo_id=?', (identifier,)).fetchone()
        if job['state'] == 'running':
            return photo
        c.execute("UPDATE photo_jobs SET state='running' WHERE photo_id=?", (identifier,))
        photo['analysis']['status'] = 'running'
        c.execute('UPDATE photos SET data=? WHERE id=?', (json.dumps({k:v for k,v in photo.items() if k not in ('id','date','caption')}), identifier))
    return await perform_photo_analysis(identifier, options.side, photo['analysis']['id'])


@app.post('/api/photos/analyze-batch', dependencies=[Depends(authorized)])
def batch_analyze(options: BatchAnalysis):
    profile = json.loads(setting('profile'))
    queued, skipped, needs_side = [], [], []
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        identifiers = options.photo_ids if options.photo_ids is not None else [r['id'] for r in c.execute('SELECT id FROM photos')]
        for identifier in dict.fromkeys(identifiers):
            row = c.execute('SELECT * FROM photos WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise HTTPException(404, 'A selected photo no longer exists.')
            data = json.loads(row['data'])
            analysis = data.get('analysis', {})
            if data.get('measurements') or analysis.get('status') == 'ready':
                skipped.append(identifier)
                continue
            side = next((s for s in (options.side, data.get('side'), analysis.get('side'), profile.get('side')) if s in ('left', 'right')), None)
            photo, added = enqueue_photo(c, identifier, side, retry=options.retry_failed)
            (queued if added else needs_side if photo.get('analysis', {}).get('status') == 'needs_side' else skipped).append(identifier)
    return {'queued': queued, 'skipped': skipped, 'needs_side': needs_side}


@app.post('/api/photos/{identifier}/apply', dependencies=[Depends(authorized)])
def apply_photo(identifier: str, review: ApplyPhoto, idempotency_key: str | None = Header(default=None)):
    fingerprint = digest('apply:' + identifier + ':' + review.model_dump_json())
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        cached = cached_operation(c, idempotency_key, fingerprint)
        if cached:
            return cached
        row = c.execute('SELECT * FROM photos WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise HTTPException(404, 'Photo not found.')
        angle = bend_angle(review.points, DATA / 'photos' / row['filename'])
        value = measurement_value(angle, review.metric, review.extension_direction)
        result = merge_entry(c, review.date, {review.metric: value}, review.expected_version, write=True)
        data = json.loads(row['data'])
        points = [p.model_dump() for p in review.points]
        analysis = data.get('analysis', {})
        assisted = analysis.get('status') == 'ready' and analysis.get('side') == review.side and analysis.get('id') == review.analysis_id
        evidence = {'entry_id': result['entry']['id'], 'date': str(review.date), 'value': value, 'metric': review.metric,
                    'extension_direction': review.extension_direction if review.metric == 'extension' else None, 'points': points,
                    'side': review.side, 'reviewed_at': time.time(), 'method': 'assisted' if assisted else 'manual',
                    'model': analysis.get('model') if assisted else None, 'model_version': analysis.get('model_version') if assisted else None,
                    'analysis_id': analysis.get('id') if assisted else None}
        data.update(points=points, angle=angle, measurement_type=review.metric, measurement_value=value,
                    extension_direction=review.extension_direction if review.metric == 'extension' else None, side=review.side)
        data.setdefault('measurements', []).append(evidence)
        c.execute('UPDATE photos SET date=?,caption=?,data=? WHERE id=?', (str(review.date), review.caption, json.dumps(data), identifier))
        result = {'entry': result['entry'], 'photo': dict(id=identifier, date=str(review.date), caption=review.caption, **data)}
        cache_operation(c, idempotency_key, fingerprint, result)
    return result


@app.delete('/api/photos/{identifier}', status_code=204, dependencies=[Depends(authorized)])
def delete_photo(identifier: str):
    with lock:
        with db() as c:
            row = c.execute('SELECT filename FROM photos WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise HTTPException(404, 'Photo not found.')
            c.execute('DELETE FROM photos WHERE id=?', (identifier,))
        (DATA / 'photos' / row['filename']).unlink(missing_ok=True)


@app.get('/api/token', dependencies=[Depends(browser_session)])
def token_status():
    return {'enabled': setting('api_token_hash') is not None}


@app.post('/api/token', dependencies=[Depends(browser_session)])
def create_token():
    token = 'stride_' + secrets.token_urlsafe(32)
    with db() as c:
        set_setting(c, 'api_token_hash', digest(token))
    return {'token': token}


@app.delete('/api/token', status_code=204, dependencies=[Depends(browser_session)])
def delete_token():
    with db() as c:
        c.execute("DELETE FROM settings WHERE key='api_token_hash'")


@app.get('/api/export/csv', dependencies=[Depends(authorized)])
def export_csv():
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=['id', *Entry.model_fields.keys()])
    writer.writeheader()
    for entry in list_entries():
        # Neutralize spreadsheet formulas in free text.
        entry['notes'] = "'" + entry['notes'] if entry['notes'].startswith(('=', '+', '-', '@', '\t', '\r')) else entry['notes']
        writer.writerow(entry)
    return Response(output.getvalue(), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="stride-entries.csv"'})


@app.get('/api/export/backup', dependencies=[Depends(browser_session)])
def export_backup():
    # No credentials or sessions in the portable archive. Photos and entries share a DB snapshot.
    archive = tempfile.TemporaryFile(dir=DATA)
    with lock, db() as c, zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        c.execute('BEGIN')
        entries = [dict(json.loads(r['data']), id=r['id']) for r in c.execute('SELECT * FROM entries ORDER BY date')]
        photos = [dict(r) for r in c.execute('SELECT * FROM photos')]
        profile = json.loads(c.execute("SELECT value FROM settings WHERE key='profile'").fetchone()['value'])
        z.writestr('journal.json', json.dumps({'version': 1, 'profile': profile, 'entries': entries,
                    'photos': [dict(id=p['id'], date=p['date'], caption=p['caption'], filename=p['filename'], **json.loads(p['data'])) for p in photos]}, indent=2))
        for photo in photos:
            path = DATA / 'photos' / photo['filename']
            if path.exists():
                z.write(path, 'photos/' + photo['filename'])
    archive.seek(0)
    def stream():
        try:
            while chunk := archive.read(64 * 1024):
                yield chunk
        finally:
            archive.close()
    return StreamingResponse(stream(), media_type='application/zip',
                             headers={'Content-Disposition': 'attachment; filename="stride-backup.zip"'})


if DIST.is_dir():
    app.mount('/', StaticFiles(directory=DIST, html=True), name='frontend')
