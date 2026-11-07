import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend import app as module

HEADERS = {'X-Stride-Request': '1'}
PASSWORD = {'password': 'test-password-12345'}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'DATA', tmp_path)
    module.attempts.clear()
    with TestClient(module.app, headers=HEADERS) as client:
        yield client


def login(client):
    assert client.post('/api/auth/setup', json=PASSWORD).status_code == 201


def test_seed_exact_dates_values_and_idempotency(client):
    login(client)
    entries = client.get('/api/entries').json()
    assert len(entries) == 32
    assert entries[0]['date'] == '2026-10-07'
    assert entries[0]['knee'] == 44.7
    assert entries[0]['above5'] == 46.1
    assert entries[0]['above15'] == 56
    assert entries[-1]['date'] == '2026-11-07'
    assert (entries[-1]['knee'], entries[-1]['above5'], entries[-1]['above15']) == (38.3, 38, 44.2)
    assert [e['date'] for e in entries if e['other_knee'] is not None] == ['2026-10-07', '2026-10-14']
    assert entries[7]['other_above5'] == 39.8
    module.initialize()
    assert len(client.get('/api/entries').json()) == 32
    client.delete('/api/entries/'+entries[0]['id'])
    module.initialize()
    assert len(client.get('/api/entries').json()) == 31  # Restarts never resurrect deleted history.


def test_authentication_setup_and_csrf(client):
    assert client.get('/api/entries').status_code == 401
    assert client.get('/api/photos').status_code == 401
    assert client.get('/api/export/csv').status_code == 401
    assert client.get('/api/auth/status').json() == {'initialized': False, 'authenticated': False}
    assert client.post('/api/auth/setup', json={'password': 'short'}).status_code == 422
    login(client)
    assert client.post('/api/auth/setup', json=PASSWORD).status_code == 409
    assert 'HttpOnly' in str(client.cookies) or client.cookies.get(module.COOKIE)
    assert client.post('/api/entries', headers={'X-Stride-Request': ''}, json={'date': '2026-10-01', 'knee': 39}).status_code == 403
    assert client.post('/api/auth/logout').status_code == 200
    assert client.get('/api/entries').status_code == 401
    assert client.post('/api/auth/login', json={'password': 'wrong-password-12345'}).status_code == 401
    assert client.post('/api/auth/login', json=PASSWORD).status_code == 200


def test_entry_crud_validation_and_missing_values(client):
    login(client)
    payload = {'date': '2026-09-01', 'knee': 42.5, 'flexion': 90, 'extension': -3, 'notes': 'test'}
    result = client.post('/api/entries', json=payload)
    assert result.status_code == 201
    entry = result.json()
    assert entry['pain_rest'] is None
    assert entry['extension'] == -3
    assert client.post('/api/entries', json=payload).status_code == 409
    for field, value in [('knee', -1), ('flexion', 181), ('pain_rest', 11), ('exercise', -2)]:
        assert client.post('/api/entries', json={**payload, field: value}).status_code == 422
    assert client.post('/api/entries', json={'date': '2026-09-02'}).status_code == 422
    assert client.post('/api/entries', json={'date': '2099-09-02', 'knee': 40}).status_code == 422
    assert client.put('/api/entries/'+entry['id'], json={**payload, 'knee': 41.2}).json()['knee'] == 41.2
    assert client.put('/api/entries/'+entry['id'], json={**payload, 'date': '2026-10-07'}).status_code == 409
    assert client.delete('/api/entries/'+entry['id']).status_code == 204
    assert client.delete('/api/entries/'+entry['id']).status_code == 404


def test_photos_are_validated_private_and_exported(client):
    login(client)
    data = io.BytesIO()
    Image.new('RGB', (200, 100), 'green').save(data, format='PNG')
    result = client.post('/api/photos', files={'file': ('test.png', data.getvalue(), 'image/png')}, data={'photo_date': '2026-10-07', 'caption': 'Test image'})
    assert result.status_code == 201
    photo = result.json()
    assert client.get('/api/photos/'+photo['id']+'/image').headers['content-type'] == 'image/jpeg'
    update = {'date': '2026-10-07', 'caption': 'Bending', 'points': [{'x': .2, 'y': .2}, {'x': .5, 'y': .5}, {'x': .7, 'y': .4}], 'angle': 80}
    measured = client.put('/api/photos/'+photo['id'], json=update)
    assert measured.status_code == 200
    computed_angle = measured.json()['angle']
    assert computed_angle != 80  # The server computes geometry rather than trusting the supplied angle.
    assert client.get('/api/photos').json()[0]['angle'] == computed_angle
    assert client.post('/api/photos', files={'file': ('bad.jpg', b'not an image')}, data={'photo_date': '2026-10-07'}).status_code == 415
    archive = client.get('/api/export/backup')
    with zipfile.ZipFile(io.BytesIO(archive.content)) as z:
        journal = json.loads(z.read('journal.json'))
        assert len(journal['entries']) == 32
        assert journal['photos'][0]['angle'] == computed_angle
        assert len(z.namelist()) == 2
        assert 'password' not in journal
    client.post('/api/auth/logout')
    assert client.get('/api/photos/'+photo['id']+'/image').status_code == 401
    client.post('/api/auth/login', json=PASSWORD)
    assert client.delete('/api/photos/'+photo['id']).status_code == 204
    assert client.get('/api/photos/'+photo['id']+'/image').status_code == 404


def test_api_tokens_rotation_revocation_and_csv(client):
    login(client)
    token = client.post('/api/token').json()['token']
    client.cookies.clear()
    headers = {'Authorization': 'Bearer '+token}
    assert client.get('/api/entries', headers=headers).status_code == 200
    assert client.post('/api/entries', headers=headers, json={'date': '2026-09-01', 'pain_rest': 0, 'notes': '=SUM(A1:A3)', 'source': 'telegram'}).status_code == 201
    assert client.post('/api/token', headers=headers).status_code == 403
    assert client.get('/api/export/backup', headers=headers).status_code == 403
    assert "'=SUM(A1:A3)" in client.get('/api/export/csv', headers=headers).text
    client.post('/api/auth/login', json=PASSWORD)
    replacement = client.post('/api/token').json()['token']
    client.cookies.clear()
    assert client.get('/api/entries', headers=headers).status_code == 401
    assert client.get('/api/entries', headers={'Authorization': 'Bearer '+replacement}).status_code == 200
    client.post('/api/auth/login', json=PASSWORD)
    assert client.delete('/api/token').status_code == 204
    client.cookies.clear()
    assert client.get('/api/entries', headers={'Authorization': 'Bearer '+replacement}).status_code == 401


def test_profile_persists_and_password_rotation_invalidates_sessions(client):
    login(client)
    profile = {'name': 'Enoch', 'start_date': '2026-10-01', 'side': 'left', 'context': 'Clinician plan', 'timezone': 'Asia/Singapore'}
    assert client.put('/api/profile', json={**profile, 'timezone': 'Invalid/Zone'}).status_code == 422
    assert client.put('/api/profile', json=profile).status_code == 200
    module.initialize()
    assert client.get('/api/profile').json() == profile
    previous_session = client.cookies.get(module.COOKIE)
    assert client.put('/api/auth/password', json={'password': 'new-password-12345'}).status_code == 200
    assert client.get('/api/entries').status_code == 200
    client.cookies.clear()
    client.cookies.set(module.COOKIE, previous_session)
    assert client.get('/api/entries').status_code == 401


def test_login_rate_limited(client):
    login(client)
    client.post('/api/auth/logout')
    for _ in range(10):
        assert client.post('/api/auth/login', json={'password': 'wrong-password-12345'}).status_code == 401
    assert client.post('/api/auth/login', json=PASSWORD).status_code == 429
