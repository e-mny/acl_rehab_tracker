import io
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from backend import app as module
from backend.tests.test_app import client, login
from integrations.pose.service import select_landmarks
from integrations.telegram.bot import Bot, ServiceError
from integrations.telegram.parser import parse_entry, photo_caption


def test_patch_preserves_fields_conflicts_and_retries(client):
    login(client)
    payload = {'date': '2026-10-07', 'values': {'knee': 43, 'pain_rest': 0, 'source': 'telegram'}}
    preview = client.post('/api/entries/preview', json=payload).json()
    assert preview['entry']['above5'] == 46.1
    assert client.get('/api/entries').json()[0]['knee'] == 44.7  # Preview is read-only.
    headers = {'If-Match': preview['version'], 'Idempotency-Key': 'test-patch-1'}
    saved = client.patch('/api/entries/by-date/2026-10-07', json=payload['values'], headers=headers)
    assert saved.status_code == 200
    assert saved.json()['above5'] == 46.1
    assert saved.json()['other_knee'] == 37
    assert saved.json()['pain_rest'] == 0
    assert client.patch('/api/entries/by-date/2026-10-07', json=payload['values'], headers=headers).json() == saved.json()
    assert client.patch('/api/entries/by-date/2026-10-07', json={'knee': 40}, headers=headers).status_code == 409
    assert client.patch('/api/entries/by-date/2026-10-07', json={'knee': 40}, headers={'If-Match': preview['version']}).status_code == 409
    for patch in ({}, {'pain_rest': 99}, {'bad_field': 3}, {'knee': -2}, {'notes': None}):
        assert client.patch('/api/entries/by-date/2026-10-07', json=patch).status_code == 422
    assert client.patch('/api/entries/by-date/2026-09-01', json={'notes': 'New note'}, headers={'If-Match': 'new'}).status_code == 200


def upload(client):
    stream = io.BytesIO()
    Image.new('RGB', (400, 200), 'white').save(stream, 'PNG')
    return client.post('/api/photos', files={'file': ('knee.png', stream.getvalue(), 'image/png')}, data={'photo_date': '2026-10-07'}, headers={'Idempotency-Key': 'photo-upload'}).json()


def test_review_required_atomic_apply_and_evidence(client):
    login(client)
    photo = upload(client)
    assert upload(client)['id'] == photo['id']
    assert len(client.get('/api/photos').json()) == 1
    preview = client.post('/api/entries/preview', json={'date': '2026-10-07', 'values': {'flexion': 90}}).json()
    points = [{'x': .2, 'y': .5}, {'x': .5, 'y': .5}, {'x': .5, 'y': .9}]
    payload = {'date': '2026-10-07', 'caption': 'Reviewed', 'points': points, 'side': 'left', 'reviewed': True, 'expected_version': preview['version']}
    assert client.post('/api/photos/'+photo['id']+'/apply', json={**payload, 'reviewed': False}).status_code == 422
    assert client.post('/api/photos/'+photo['id']+'/apply', json={**payload, 'expected_version': 'stale'}).status_code == 409
    assert client.get('/api/photos').json()[0].get('measurements') is None
    result = client.post('/api/photos/'+photo['id']+'/apply', json=payload, headers={'Idempotency-Key': 'apply-1'})
    assert result.status_code == 200
    assert result.json()['entry']['flexion'] == 90
    assert result.json()['entry']['knee'] == 44.7
    assert result.json()['entry']['notes'].startswith('Imported from message history.')
    assert result.json()['photo']['measurements'][0]['entry_id'] == result.json()['entry']['id']
    assert client.post('/api/photos/'+photo['id']+'/apply', json=payload, headers={'Idempotency-Key': 'apply-1'}).json() == result.json()
    assert len(client.get('/api/photos').json()[0]['measurements']) == 1


def test_pose_analysis_proposals_do_not_change_entries(client, monkeypatch):
    login(client)
    photo = upload(client)
    assert client.post('/api/photos/'+photo['id']+'/analyze', json={'side': 'left'}).status_code == 503
    monkeypatch.setenv('POSE_ANALYZER_URL', 'http://pose:8001')
    points = [{'x': .2, 'y': .5}, {'x': .5, 'y': .5}, {'x': .5, 'y': .9}]
    class Analyzer:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            return httpx.Response(200, json={'points': points, 'confidence': .91, 'model': 'Test Pose', 'model_version': 'test-v1'}, request=httpx.Request('POST', 'http://pose/analyze'))
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: Analyzer())
    response = client.post('/api/photos/'+photo['id']+'/analyze', json={'side': 'left'})
    assert response.status_code == 200
    assert response.json()['analysis']['status'] == 'ready'
    assert response.json()['analysis']['angle'] == 90
    assert client.get('/api/entries').json()[0]['flexion'] is None
    assert response.json().get('points') is None  # Original manual annotations also remain untouched.
    client.put('/api/photos/'+photo['id'], json={'date': photo['date'], 'caption': 'Adjusted', 'points': points})
    assert client.get('/api/photos').json()[0]['analysis']['model_version'] == 'test-v1'


def test_pose_rejects_missing_occluded_and_multiple_people():
    from fastapi import HTTPException
    points = [SimpleNamespace(x=.5, y=.5, visibility=.9, presence=.9) for _ in range(33)]
    assert select_landmarks(SimpleNamespace(pose_landmarks=[points]), 'left')['confidence'] == .9
    for poses in ([], [points, points]):
        with pytest.raises(HTTPException): select_landmarks(SimpleNamespace(pose_landmarks=poses), 'left')
    points[25].visibility = .2
    with pytest.raises(HTTPException): select_landmarks(SimpleNamespace(pose_landmarks=[points]), 'left')


def test_parser_existing_format_and_errors():
    parsed = parse_entry('7/10/26\nbad side:\njust above knee cap: 44.7\n5cm above knee cap: 46.1\n15cm above knee cap: 56\ngood side:\n37cm\n42cm\n50cm', '2026-11-07')
    assert parsed['date'] == '2026-10-07'
    assert parsed['values'] == {'knee': 44.7, 'above5': 46.1, 'above15': 56, 'other_knee': 37, 'other_above5': 42, 'other_above15': 50, 'source': 'telegram'}
    assert parse_entry('/log\npain: 0\nsteps: 2500\nnotes: A check-in\nMore detail', '2026-11-07')['values']['notes'] == 'A check-in\nMore detail'
    for text in ('knee: 38mm', 'kne: 38', 'steps: 1.5', 'knee: 38\nknee: 39', '7/10/26\n8/10/26\nknee: 38', '31/2/26\nknee: 38', 'knee: 38,3'):
        with pytest.raises(ValueError): parse_entry(text, '2026-11-07')
    assert photo_caption('7/11/26\nSide-on reference', '2026-10-01') == ('2026-11-07', 'Side-on reference')


def make_bot(client, tmp_path):
    login(client)
    token = client.post('/api/token').json()['token']
    client.cookies.clear()
    sent = []
    image = io.BytesIO()
    Image.new('RGB', (50, 100), 'white').save(image, 'PNG')
    def route(request):
        if request.url.host == 'stride':
            response = client.request(request.method, request.url.raw_path.decode(), content=request.read(), headers=dict(request.headers))
            return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))
        if request.url.path.startswith('/file/'):
            return httpx.Response(200, content=image.getvalue())
        payload = json.loads(request.content)
        method = request.url.path.rsplit('/', 1)[-1]
        if method == 'sendMessage': sent.append(payload['text'])
        return httpx.Response(200, json={'ok': True, 'result': {'file_path': 'photos/file.png'} if method == 'getFile' else {}})
    http = httpx.Client(transport=httpx.MockTransport(route))
    return Bot('fake-token', 123, 'http://stride', token, tmp_path / 'bot', client=http), sent, token


def message(update_id, text='', **extra):
    return {'update_id': update_id, 'message': {'from': {'id': 123}, 'chat': {'type': 'private', 'id': 123}, 'date': 1794024000, 'text': text, **extra}}


def callback(bot, action='save'):
    row = bot.db.execute('SELECT * FROM drafts ORDER BY created DESC LIMIT 1').fetchone()
    bot.process({'update_id': 200, 'callback_query': {'id': 'callback', 'from': {'id': 123}, 'message': {'chat': {'type': 'private', 'id': 123}}, 'data': action + ':' + row['id']}})
    return row['id']


def test_bot_confirmation_allowlist_restart_and_retry(client, tmp_path):
    bot, sent, token = make_bot(client, tmp_path)
    bad = message(1, 'knee: 40'); bad['message']['from']['id'] = 999
    bot.process(bad)
    assert not sent
    bot.process(message(2, '7/10/26\nknee: 43\npain: 0'))
    assert '44.7 → 43.0' in sent[-1]
    assert client.get('/api/entries', headers={'Authorization': 'Bearer '+token}).json()[0]['knee'] == 44.7
    original = bot.telegram
    def expired_ack(method, **payload):
        if method == 'answerCallbackQuery':
            raise ServiceError(400, 'Expired callback acknowledgement')
        return original(method, **payload)
    bot.telegram = expired_ack
    identifier = callback(bot)
    saved = client.get('/api/entries', headers={'Authorization': 'Bearer '+token}).json()[0]
    assert saved['knee'] == 43 and saved['pain_rest'] == 0 and saved['above5'] == 46.1
    # Simulate a crash after the API committed but before the worker marked the draft saved.
    bot.db.execute("UPDATE drafts SET status='pending' WHERE id=?", (identifier,)); bot.db.commit()
    callback(bot)
    assert bot.db.execute('SELECT status FROM drafts').fetchone()['status'] == 'saved'
    bot.set_offset(201)
    restarted = Bot('fake-token', 123, 'http://stride', token, tmp_path / 'bot', client=bot.client)
    assert restarted.offset() == 201
    assert restarted.db.execute('SELECT status FROM drafts').fetchone()['status'] == 'saved'
    restarted.process(message(201, '7/10/26\npain: 3'))
    callback(restarted, 'cancel')
    assert client.get('/api/entries', headers={'Authorization': 'Bearer '+token}).json()[0]['pain_rest'] == 0


def test_bot_conflict_and_photo_confirmation(client, tmp_path):
    bot, sent, token = make_bot(client, tmp_path)
    auth = {'Authorization': 'Bearer '+token}
    bot.process(message(3, '7/10/26\nknee: 43'))
    client.patch('/api/entries/by-date/2026-10-07', json={'above5': 45}, headers=auth)
    callback(bot)
    assert any('journal changed' in text for text in sent)
    assert client.get('/api/entries', headers=auth).json()[0]['knee'] == 44.7
    callback(bot)
    assert client.get('/api/entries', headers=auth).json()[0]['above5'] == 45
    bot.process(message(4, photo=[{'file_id': 'photo-1', 'file_size': 100}], caption='7/11/26\nSide-on'))
    assert client.get('/api/photos', headers=auth).json() == []
    identifier = callback(bot)
    assert len(client.get('/api/photos', headers=auth).json()) == 1
    assert client.get('/api/photos', headers=auth).json()[0]['source'] == 'telegram'
    bot.db.execute("UPDATE drafts SET status='pending' WHERE id=?", (identifier,)); bot.db.commit()
    callback(bot)
    assert len(client.get('/api/photos', headers=auth).json()) == 1
