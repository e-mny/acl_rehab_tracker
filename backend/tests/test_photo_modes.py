import asyncio
import io
import json

import httpx
import pytest
from PIL import Image

from backend import app as module
from backend.tests.test_app import client, login


def photo(client, key='new-photo'):
    image = io.BytesIO()
    Image.new('RGB', (400, 200), '#dedfda').save(image, 'PNG')
    return client.post('/api/photos', files={'file': ('photo.png', image.getvalue(), 'image/png')},
                       data={'photo_date': '2026-11-06'}, headers={'Idempotency-Key': key}).json()


POINTS = [{'x': .8, 'y': .5}, {'x': .5, 'y': .5}, {'x': .2, 'y': .56}]


def test_extension_direction_is_explicit_and_flexion_is_preserved(client):
    login(client)
    client.patch('/api/entries/by-date/2026-11-06', json={'flexion': 110})
    p = photo(client)
    version = client.post('/api/entries/preview', json={'date': p['date'], 'values': {'extension': 5.7}}).json()['version']
    review = {'date': p['date'], 'caption': 'Extension check', 'side': 'right', 'metric': 'extension',
              'points': POINTS, 'reviewed': True, 'expected_version': version}
    assert client.post('/api/photos/'+p['id']+'/apply', json=review).status_code == 422
    positive = client.post('/api/photos/'+p['id']+'/apply', json={**review, 'extension_direction': 'deficit'})
    assert positive.status_code == 200
    assert positive.json()['entry']['extension'] == pytest.approx(5.7)
    assert positive.json()['entry']['flexion'] == 110
    assert positive.json()['photo']['measurements'][0]['metric'] == 'extension'
    version = client.post('/api/entries/preview', json={'date': p['date'], 'values': {'extension': -5.7}}).json()['version']
    negative = client.post('/api/photos/'+p['id']+'/apply', json={**review, 'expected_version': version, 'extension_direction': 'hyperextension'})
    assert negative.json()['entry']['extension'] == pytest.approx(-5.7)
    assert negative.json()['entry']['flexion'] == 110
    assert negative.json()['entry']['knee'] == 38.1
    assert len(negative.json()['photo']['measurements']) == 2
    assert negative.json()['photo']['measurement_value'] == -5.7


def test_annotations_do_not_log_or_move_legacy_measurements(client):
    login(client)
    p = photo(client)
    client.patch('/api/entries/by-date/2026-11-06', json={'flexion': 6})
    before = client.get('/api/entries').json()
    result = client.put('/api/photos/'+p['id'], json={'date': p['date'], 'points': POINTS, 'measurement_type': 'extension', 'extension_direction': 'deficit'})
    assert result.status_code == 200
    assert result.json()['measurement_value'] == 5.7
    assert client.get('/api/entries').json() == before
    assert client.get('/api/entries').json()[-2]['flexion'] == 6


class OfflineAnalyzer:
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def get(self, *args, **kwargs): return httpx.Response(503)


def test_upload_and_existing_photos_queue_automatically_and_survive_restart(client, monkeypatch):
    login(client)
    monkeypatch.setenv('POSE_ANALYZER_URL', 'http://pose:8001')
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: OfflineAnalyzer())
    # A photo uploaded before the leg was configured becomes eligible once the user sets it.
    p = photo(client)
    assert p['analysis']['status'] == 'needs_side'
    profile = client.get('/api/profile').json()
    client.put('/api/profile', json={**profile, 'side': 'right'})
    first = client.get('/api/photos').json()[0]
    assert first['analysis']['status'] == 'queued'
    assert first['analysis']['side'] == 'right'
    second = photo(client, 'second-photo')
    assert second['analysis']['status'] == 'queued'
    # In-flight work is recovered after process replacement; manual alignment is preserved.
    client.put('/api/photos/'+p['id'], json={'date': p['date'], 'points': POINTS})
    with module.db() as c:
        c.execute("UPDATE photo_jobs SET state='running'")
    module.initialize()
    with module.db() as c:
        assert all(r['state'] == 'queued' for r in c.execute('SELECT state FROM photo_jobs'))
    first = next(x for x in client.get('/api/photos').json() if x['id'] == p['id'])
    assert first['analysis']['status'] == 'queued'
    assert first['points'] == POINTS
    assert client.get('/api/entries').json()[-2]['extension'] is None


def test_batch_retries_failed_skips_reviewed_and_deduplicates_jobs(client, monkeypatch):
    login(client)
    monkeypatch.setenv('POSE_ANALYZER_URL', 'http://pose:8001')
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: OfflineAnalyzer())
    one, two = photo(client), photo(client, 'another-photo')
    with module.db() as c:
        c.execute('UPDATE photos SET data=? WHERE id=?', (json.dumps({'analysis': {'status':'failed', 'side':'right'}}), one['id']))
        c.execute('UPDATE photos SET data=? WHERE id=?', (json.dumps({'measurements': [{'metric':'flexion','value':6}]}), two['id']))
    before = client.get('/api/entries').json()
    result = client.post('/api/photos/analyze-batch', json={'side':'right'}).json()
    assert result['queued'] == [one['id']]
    assert result['skipped'] == [two['id']]
    client.post('/api/photos/analyze-batch', json={'side':'right'})
    with module.db() as c:
        assert c.execute('SELECT COUNT(*) FROM photo_jobs').fetchone()[0] == 1
    assert client.get('/api/entries').json() == before
    assert client.post('/api/photos/analyze-batch', json={'photo_ids':['missing'], 'side':'right'}).status_code == 404


def test_background_proposal_keeps_manual_alignment_and_does_not_log(client, monkeypatch):
    login(client)
    monkeypatch.setenv('POSE_ANALYZER_URL','http://pose:8001')
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: OfflineAnalyzer())
    p = photo(client)
    queued = client.post('/api/photos/'+p['id']+'/queue', json={'side':'right'}).json()
    client.put('/api/photos/'+p['id'], json={'date':p['date'], 'points':POINTS})
    class ReadyAnalyzer(OfflineAnalyzer):
        async def post(self, *args, **kwargs):
            return httpx.Response(200, request=httpx.Request('POST','http://pose/analyze'), json={'points':[{'x':.1,'y':.2},{'x':.5,'y':.5},{'x':.8,'y':.7}], 'confidence':.9,'model':'test','model_version':'1'})
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kwargs:ReadyAnalyzer())
    result = asyncio.run(module.perform_photo_analysis(p['id'],'right',queued['analysis']['id']))
    assert result['analysis']['status'] == 'ready'
    assert result['points'] == POINTS
    assert client.get('/api/entries').json()[-2]['flexion'] is None
    assert client.get('/api/entries').json()[-2]['extension'] is None


def test_failure_reason_is_preserved_for_manual_fallback(client, monkeypatch):
    login(client)
    p = photo(client)
    monkeypatch.setenv('POSE_ANALYZER_URL','http://pose:8001')
    class CroppedAnalyzer(OfflineAnalyzer):
        async def post(self, *args, **kwargs):
            return httpx.Response(422, json={'detail':{'code':'no_pose','message':'No full-body pose was found. Align the visible thigh and shin axes manually.'}})
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kwargs:CroppedAnalyzer())
    result = client.post('/api/photos/'+p['id']+'/analyze',json={'side':'right'})
    assert result.status_code == 422
    assert 'thigh and shin' in result.json()['detail']
    assert client.get('/api/photos').json()[0]['analysis']['status'] == 'failed'
