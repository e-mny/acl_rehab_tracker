"""Synthetic steps, clocks and authentication; never contacts Garmin."""
from datetime import UTC, date, datetime, timedelta
import logging
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from backend import app as module
from backend.tests.test_app import client, login
from integrations.garmin.worker import (GarminProvider, Journal, SyncError, Worker,
    batches, daily_totals, due_day, exclusive_worker, next_sync)


START = date(2020, 1, 15)
ZONE = ZoneInfo('Asia/Singapore')


def at(day, hour=12, minute=0):
    return datetime(2020, 1, day, hour, minute, tzinfo=ZONE)


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.missing = set()
        self.values = {}
        self.summary_values = {}
        self.summary_calls = []
        self.error = None

    def get_daily_steps(self, start, end):
        self.calls.append((start, end))
        if self.error:
            raise SyncError(self.error)
        days = (date.fromisoformat(start) + timedelta(days=n)
                for n in range((date.fromisoformat(end) - date.fromisoformat(start)).days + 1))
        return [{'calendarDate': day.isoformat(), 'totalSteps': self.values.get(day, 1000)}
                for day in days if day not in self.missing]

    def get_user_summary(self, day):
        self.summary_calls.append(day)
        parsed = date.fromisoformat(day)
        return {'calendarDate': day, 'totalSteps': self.summary_values.get(parsed, self.values.get(parsed, 1000))}


@pytest.fixture
def integration(client, tmp_path):
    login(client)
    token = client.post('/api/token').json()['token']
    client.cookies.clear()
    requests = []
    failure = {'date': None}
    def route(request):
        requests.append(request)
        if request.url.path.endswith('/steps') and failure['date']:
            import json
            if json.loads(request.content)['date'] == failure['date']:
                return httpx.Response(503, json={'detail': 'PRIVATE upstream error'})
        response = client.request(request.method, request.url.raw_path.decode(), content=request.read(), headers=dict(request.headers))
        return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))
    http = httpx.Client(transport=httpx.MockTransport(route))
    journal = Journal('http://stride', token, client=http)
    provider = FakeProvider()
    worker = Worker(tmp_path / 'worker', START, journal, provider_factory=lambda _: provider, sleeper=lambda _: None)
    yield SimpleNamespace(worker=worker, provider=provider, journal=journal,
                          auth={'Authorization': 'Bearer ' + token}, requests=requests, failure=failure)
    worker.db.close()
    http.close()


def test_steps_endpoint_requires_auth_and_rejects_non_steps_fields(client):
    payload = {'date': START.isoformat(), 'steps': 0}
    assert client.post('/api/integrations/garmin/steps', json=payload).status_code == 401
    login(client)
    for patch in ({'steps': None}, {'steps': -1}, {'steps': 1.5}, {'steps': True}, {'steps': 100001}, {'notes': 'Must not change notes'}):
        assert client.post('/api/integrations/garmin/steps', json=payload | patch).status_code == 422
    assert client.post('/api/integrations/garmin/heartbeat', json={'state': 'idle', 'raw_tokens': 'forbidden'}).status_code == 422


def test_import_preserves_all_other_fields_and_supports_zero(client, integration):
    original = client.post('/api/entries', json={'date': START.isoformat(), 'steps': 99, 'knee': 41.2,
        'flexion': 95, 'notes': 'Synthetic rehab note', 'source': 'telegram'}, headers=integration.auth).json()
    payload = {'date': START.isoformat(), 'steps': 0}
    first = client.post('/api/integrations/garmin/steps', json=payload, headers=integration.auth).json()
    repeated = client.post('/api/integrations/garmin/steps', json=payload, headers=integration.auth).json()
    assert first == repeated == original | {'steps': 0}
    new = client.post('/api/integrations/garmin/steps', json={'date': '2020-01-16', 'steps': 1234}, headers=integration.auth).json()
    assert new['source'] == 'api' and new['notes'] == ''
    with module.db() as db:
        assert db.execute('SELECT count(*) FROM garmin_steps').fetchone()[0] == 2


def test_schedule_uses_local_2359_and_handles_dst():
    assert due_day(at(16, 23, 58)) == date(2020, 1, 15)
    assert due_day(at(16, 23, 59)) == date(2020, 1, 16)
    assert due_day(at(17, 0, 0)) == date(2020, 1, 16)
    assert next_sync(at(16, 12)).hour == 23 and next_sync(at(16, 12)).minute == 59
    assert next_sync(at(16, 23, 59)).date() == date(2020, 1, 17)
    ny = ZoneInfo('America/New_York')
    before = datetime(2020, 3, 7, 23, 59, tzinfo=ny)
    after = next_sync(before)
    assert after.hour == 23 and after.date() == date(2020, 3, 8)
    assert after.utcoffset() != before.utcoffset()


def test_backfill_and_nightly_sync_use_right_dates_and_leave_notes_alone(client, integration):
    x = integration
    assert x.worker.tick(at(17, 12))
    assert x.provider.calls == [('2020-01-15', '2020-01-16')]
    assert x.worker.tick(at(17, 23, 58))
    assert len(x.provider.calls) == 1
    assert x.worker.tick(at(17, 23, 59))
    assert x.provider.calls[-1] == ('2020-01-15', '2020-01-17')
    assert x.provider.summary_calls == ['2020-01-17']
    entries = client.get('/api/entries', headers=x.auth).json()
    imported = [e for e in entries if e['date'].startswith('2020-')]
    assert [e['date'] for e in imported] == ['2020-01-15', '2020-01-16', '2020-01-17']
    assert all(e['steps'] == 1000 and e['knee'] is None and e['notes'] == '' for e in imported)
    status = client.get('/api/integrations', headers=x.auth).json()['garmin']
    assert status['online'] and status['state'] == 'idle' and status['imported_days'] == 3
    assert status['timezone'] == 'Asia/Singapore'
    assert datetime.fromtimestamp(status['next_sync'], ZONE) == at(18, 23, 59)


def test_restart_catches_up_and_limits_recent_reconciliation(integration, tmp_path):
    x = integration
    assert x.worker.tick(at(16, 12))
    restarted = Worker(tmp_path / 'worker', START, x.journal, provider_factory=lambda _: x.provider, sleeper=lambda _: None)
    try:
        assert restarted.tick(at(16, 13))
        assert len(x.provider.calls) == 1
        assert restarted.tick(at(30, 12))
        assert restarted.db.execute('SELECT count(*) FROM days').fetchone()[0] == 15
        assert restarted.tick(at(31, 12))
        assert x.provider.calls[-1] == ('2020-01-24', '2020-01-30')
    finally:
        restarted.db.close()


def test_2359_uses_current_day_summary_even_when_history_lags(client, integration):
    x = integration
    x.provider.values[START] = 99
    x.provider.summary_values[START] = 1234
    assert x.worker.tick(at(15, 23, 59))
    entry = next(e for e in client.get('/api/entries', headers=x.auth).json() if e['date'] == START.isoformat())
    assert entry['steps'] == 1234


def test_missing_days_retry_without_inventing_zero_and_late_steps_are_refreshed(client, integration):
    x = integration
    x.provider.missing.add(START)
    assert x.worker.tick(at(16, 12))
    assert x.worker.pending == 1
    assert not any(e['date'] == START.isoformat() for e in client.get('/api/entries', headers=x.auth).json())
    assert x.worker.tick(at(16, 12, 30))
    assert len(x.provider.calls) == 1
    x.provider.missing.clear()
    x.provider.values[START] = 0
    assert x.worker.tick(at(16, 13))
    assert x.worker.pending == 0
    x.provider.values[START] = 1300
    assert x.worker.tick(at(16, 23, 59))
    entry = next(e for e in client.get('/api/entries', headers=x.auth).json() if e['date'] == START.isoformat())
    assert entry['steps'] == 1300


def test_partial_journal_failure_preserves_checkpoints_and_retries_safely(client, integration, caplog):
    x = integration
    x.failure['date'] = '2020-01-16'
    with caplog.at_level(logging.WARNING):
        assert not x.worker.tick(at(17, 12))
    assert x.worker.db.execute('SELECT count(*) FROM days').fetchone()[0] == 1
    assert 'PRIVATE' not in caplog.text
    x.failure['date'] = None
    assert x.worker.tick(at(17, 12, 6))
    entries = [e for e in client.get('/api/entries', headers=x.auth).json() if e['date'].startswith('2020-')]
    assert len(entries) == 2 and all(e['steps'] == 1000 for e in entries)


@pytest.mark.parametrize('code', ['rate_limited', 'needs_login'])
def test_provider_errors_back_off_across_restarts(integration, tmp_path, code):
    x = integration
    x.provider.error = code
    assert not x.worker.tick(at(17, 12))
    calls = len(x.provider.calls)
    restarted = Worker(tmp_path / 'worker', START, x.journal, provider_factory=lambda _: x.provider)
    try:
        assert not restarted.tick(at(17, 12, 5))
        assert len(x.provider.calls) == calls
        x.provider.error = None
        assert restarted.tick(at(17, 12, 31))
    finally:
        restarted.db.close()


def test_force_sync_refreshes_older_backfill_and_never_touches_future_days(integration):
    x = integration
    assert x.worker.tick(at(30, 12))
    x.provider.calls.clear()
    assert x.worker.tick(at(30, 12), force=True)
    assert x.provider.calls == [('2020-01-15', '2020-01-29')]


def test_batches_respect_28_days_and_do_not_fetch_gaps():
    days = {START + timedelta(days=n) for n in range(60)} | {START + timedelta(days=100)}
    result = list(batches(days))
    assert [len(batch) for batch in result] == [28, 28, 4, 1]
    assert {day for batch in result for day in batch} == days


@pytest.mark.parametrize('payload', [None, {}, [{'calendarDate': 'bad', 'totalSteps': 1}],
    [{'calendarDate': '2020-01-15', 'totalSteps': -1}], [{'calendarDate': '2020-01-15', 'totalSteps': True}],
    [{'calendarDate': '2020-01-15', 'totalSteps': 1.5}], [{'calendarDate': '2020-01-16', 'totalSteps': 1}]])
def test_invalid_provider_data_never_imports(payload):
    with pytest.raises(SyncError, match='invalid_data'):
        daily_totals(payload, START, START)


def test_null_steps_are_missing_and_zero_is_real():
    assert daily_totals([{'calendarDate': START.isoformat(), 'totalSteps': None}], START, START) == {}
    assert daily_totals([{'calendarDate': START.isoformat(), 'totalSteps': 0}], START, START) == {START: 0}


def test_exclusive_worker_prevents_concurrent_login_or_sync(tmp_path):
    with exclusive_worker(tmp_path):
        with pytest.raises(SystemExit, match='Another Garmin worker'):
            with exclusive_worker(tmp_path):
                pass


def test_missing_session_requires_interactive_setup_without_network(tmp_path, monkeypatch):
    import sys
    class AuthError(Exception): pass
    class ConnectionError(Exception): pass
    class RateError(Exception): pass
    def unexpected(*args, **kwargs):
        pytest.fail('Must not create an authenticated client without saved tokens.')
    monkeypatch.setitem(sys.modules, 'garminconnect', SimpleNamespace(Garmin=unexpected,
        GarminConnectAuthenticationError=AuthError, GarminConnectConnectionError=ConnectionError,
        GarminConnectTooManyRequestsError=RateError))
    with pytest.raises(SyncError, match='needs_login'):
        GarminProvider(tmp_path)
