"""Scheduled, steps-only Garmin imports with persistent backfill checkpoints."""
import argparse
from contextlib import contextmanager, suppress
from datetime import UTC, date, datetime, time as wall_time, timedelta
import fcntl
from getpass import getpass
import logging
import os
from pathlib import Path
import signal
import sqlite3
import threading
import time
from zoneinfo import ZoneInfo

import httpx


logger = logging.getLogger('stride-garmin')
SCHEDULE = wall_time(23, 59)


class SyncError(Exception):
    """A safe public error code, never a library exception or credential."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def due_day(now):
    return now.date() if now.time().replace(tzinfo=None) >= SCHEDULE else now.date() - timedelta(days=1)


def next_sync(now):
    scheduled = datetime.combine(now.date(), SCHEDULE, tzinfo=now.tzinfo)
    if scheduled <= now:
        scheduled = datetime.combine(now.date() + timedelta(days=1), SCHEDULE, tzinfo=now.tzinfo)
    return scheduled


def day_range(start, end):
    while start <= end:
        yield start
        start += timedelta(days=1)


def batches(days):
    """Keep requests contiguous and within Garmin's 28-day range limit."""
    batch = []
    for day in sorted(days):
        if batch and (len(batch) == 28 or day != batch[-1] + timedelta(days=1)):
            yield batch
            batch = []
        batch.append(day)
    if batch:
        yield batch


def daily_totals(payload, start, end):
    if not isinstance(payload, list):
        raise SyncError('invalid_data')
    totals = {}
    for record in payload:
        try:
            day = date.fromisoformat(record['calendarDate'])
            value = record.get('totalSteps')
        except (TypeError, KeyError, ValueError, AttributeError):
            raise SyncError('invalid_data') from None
        if not start <= day <= end or day in totals:
            raise SyncError('invalid_data')
        if value is None:
            continue
        if type(value) is not int or not 0 <= value <= 100000:
            raise SyncError('invalid_data')
        totals[day] = value
    return totals


class GarminProvider:
    def __init__(self, token_dir, *, interactive=False):
        from garminconnect import Garmin, GarminConnectAuthenticationError, GarminConnectConnectionError, GarminConnectTooManyRequestsError
        self.auth_error = GarminConnectAuthenticationError
        self.connection_error = GarminConnectConnectionError
        self.rate_error = GarminConnectTooManyRequestsError
        self.token_dir = Path(token_dir)
        self.token_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not interactive and not (self.token_dir / 'garmin_tokens.json').is_file():
            raise SyncError('needs_login')
        if interactive:
            email = getpass('Garmin email: ').strip()
            password = getpass('Garmin password: ')
            self.client = Garmin(email=email, password=password, prompt_mfa=lambda: getpass('Garmin MFA code: ').strip())
            password = None
        else:
            self.client = Garmin()
        self.call(self.client.login, str(self.token_dir))
        # Explicitly persist refreshed sessions as well as the initial login.
        self.call(self.client.client.dump, str(self.token_dir))

    def call(self, method, *args):
        try:
            return method(*args)
        except self.auth_error:
            raise SyncError('needs_login') from None
        except self.rate_error:
            raise SyncError('rate_limited') from None
        except self.connection_error:
            raise SyncError('garmin_connection') from None

    def get_daily_steps(self, start, end):
        result = self.call(self.client.get_daily_steps, start, end)
        self.call(self.client.client.dump, str(self.token_dir))
        return result

    def get_user_summary(self, day):
        result = self.call(self.client.get_user_summary, day)
        self.call(self.client.client.dump, str(self.token_dir))
        return result


class Journal:
    def __init__(self, url, token, client=None):
        self.url, self.token = url.rstrip('/'), token
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)

    def request(self, method, path, payload=None):
        try:
            response = self.client.request(method, self.url + '/api' + path,
                headers={'Authorization': 'Bearer ' + self.token}, json=payload)
        except httpx.HTTPError:
            raise SyncError('journal_connection') from None
        if response.status_code in (401, 403):
            raise SyncError('journal_auth')
        if not response.is_success:
            raise SyncError('journal_connection')
        try:
            return response.json()
        except ValueError:
            raise SyncError('journal_connection') from None


class Worker:
    def __init__(self, directory, start_date, journal, provider_factory=GarminProvider, *, sleeper=time.sleep):
        self.directory, self.start_date, self.journal = Path(directory), start_date, journal
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.directory / 'sync.sqlite3')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS days (date TEXT PRIMARY KEY, state TEXT NOT NULL, steps INTEGER, retry_at REAL NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        self.provider_factory, self.provider, self.sleeper = provider_factory, None, sleeper
        self.timezone = 'UTC'
        self.pending = 0

    def get(self, key, default=''):
        row = self.db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        return row['value'] if row else default

    def set(self, key, value):
        self.db.execute('INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))
        self.db.commit()

    def heartbeat(self, now, state='idle', error=None):
        self.journal.request('POST', '/integrations/garmin/heartbeat', {
            'state': state, 'start_date': self.start_date.isoformat(), 'timezone': self.timezone,
            'next_sync': next_sync(now.astimezone(ZoneInfo(self.timezone))).timestamp(),
            'pending_days': self.pending, 'error_code': error})

    def tick(self, now=None, *, force=False):
        now = now or datetime.now(UTC)
        try:
            profile = self.journal.request('GET', '/profile')
            zone = ZoneInfo(profile['timezone'])
            self.timezone = zone.key
            local = now.astimezone(zone)
            end = due_day(local)
            existing = {date.fromisoformat(row['date']): row for row in self.db.execute('SELECT * FROM days')}
            self.pending = sum(day not in existing or existing[day]['state'] != 'synced' for day in day_range(self.start_date, end))
            if not force and float(self.get('retry_until', '0')) > now.timestamp():
                code = self.get('error_code', 'unexpected')
                self.heartbeat(now, state=code if code in ('needs_login', 'rate_limited') else 'error', error=code)
                return False
            cycle = self.timezone + '/' + end.isoformat()
            new_cycle = self.get('cycle') != cycle
            candidates = {day for day in day_range(self.start_date, end)
                          if day not in existing or existing[day]['state'] != 'synced' and existing[day]['retry_at'] <= now.timestamp()}
            if force:
                candidates.update(day_range(self.start_date, end))
            elif new_cycle:
                candidates.update(day_range(max(self.start_date, end - timedelta(days=6)), end))
            if candidates:
                self.heartbeat(now, 'syncing')
                if self.provider is None:
                    self.provider = self.provider_factory(self.directory / 'tokens')
                for index, batch in enumerate(batches(candidates)):
                    if index:
                        self.sleeper(1)
                    totals = daily_totals(self.provider.get_daily_steps(batch[0].isoformat(), batch[-1].isoformat()), batch[0], batch[-1])
                    if local.date() in batch:
                        # Historical statistics may lag for the current day.
                        # Use its daily summary for the 23:59 snapshot instead.
                        today = local.date()
                        summary = self.provider.get_user_summary(today.isoformat())
                        totals.pop(today, None)
                        totals.update(daily_totals([summary], today, today))
                    for day in batch:
                        if day in totals:
                            self.journal.request('POST', '/integrations/garmin/steps', {'date': day.isoformat(), 'steps': totals[day]})
                            self.db.execute('INSERT INTO days VALUES (?,?,?,0) ON CONFLICT(date) DO UPDATE SET state=excluded.state,steps=excluded.steps,retry_at=0',
                                            (day.isoformat(), 'synced', totals[day]))
                        else:
                            self.db.execute("INSERT INTO days VALUES (?,'missing',NULL,?) ON CONFLICT(date) DO UPDATE SET state='missing',retry_at=excluded.retry_at",
                                            (day.isoformat(), now.timestamp() + 3600))
                        self.db.commit()  # Crash/retry repeats a steps-only assignment safely.
                self.pending = self.db.execute("SELECT COUNT(*) FROM days WHERE date>=? AND date<=? AND state!='synced'", (self.start_date.isoformat(), end.isoformat())).fetchone()[0]
            self.set('cycle', cycle)
            self.set('retry_until', 0)
            self.set('error_code', '')
            self.heartbeat(now)
            return True
        except Exception as error:
            code = error.code if isinstance(error, SyncError) else 'unexpected'
            self.provider = None
            self.set('error_code', code)
            self.set('retry_until', now.timestamp() + (1800 if code in ('needs_login', 'rate_limited') else 300))
            with suppress(Exception):
                self.heartbeat(now, state=code if code in ('needs_login', 'rate_limited') else 'error', error=code)
            logger.warning('Garmin import paused (%s). Retry is scheduled; credentials and response bodies are not logged.', code)
            return False


@contextmanager
def exclusive_worker(directory):
    Path(directory).mkdir(parents=True, exist_ok=True, mode=0o700)
    with (Path(directory) / 'worker.lock').open('w') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Another Garmin worker/login is active. Stop the Garmin service before running a one-off command.') from None
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--login', action='store_true', help='Interactively store a Garmin session, including MFA if required.')
    mode.add_argument('--sync', action='store_true', help='Backfill/refresh all due days once, then exit.')
    args = parser.parse_args()
    os.umask(0o077)
    logging.basicConfig(level=logging.WARNING)
    # Upstream exceptions/logs may contain account details and token URLs.
    for name in ('garminconnect', 'curl_cffi', 'requests', 'urllib3', 'httpx', 'httpcore'):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    directory = Path(os.environ.get('GARMIN_DATA_DIR', './garmin-data'))
    with exclusive_worker(directory):
        if args.login:
            try:
                GarminProvider(directory / 'tokens', interactive=True)
            except Exception:
                raise SystemExit('Garmin login failed. Check the credentials/MFA or wait if rate limited; private details were not logged.') from None
            print('Garmin session saved. Start the Garmin service to import steps.')
            return
        try:
            start = date.fromisoformat(os.environ.get('GARMIN_START_DATE', ''))
        except ValueError:
            raise SystemExit('Set GARMIN_START_DATE in .env to the first day to import, using YYYY-MM-DD.') from None
        token = os.environ.get('STRIDE_API_TOKEN', '').strip()
        if not token:
            raise SystemExit('Set STRIDE_API_TOKEN in .env before starting the Garmin worker.')
        journal = Journal(os.environ.get('STRIDE_API_URL', 'http://stride:8000'), token)
        worker = Worker(directory, start, journal)
        stop = threading.Event()
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: stop.set())
        try:
            if args.sync:
                if not worker.tick(force=True):
                    raise SystemExit('Import incomplete. Inspect the Garmin integration status and retry after resolving the connection/login issue.')
                if worker.pending:
                    print('Available steps imported; some days have no Garmin reading yet and will be retried by the service.')
                else:
                    print('All due step totals imported.')
                return
            while not stop.is_set():
                worker.tick()
                now = datetime.now(ZoneInfo(worker.timezone))
                delay = min(60, max(1, next_sync(now).timestamp() - now.timestamp()))
                # If a request crossed the nightly boundary, process the new
                # day immediately rather than waiting past the 23:59 minute.
                if worker.get('cycle') != worker.timezone + '/' + due_day(now).isoformat() and float(worker.get('retry_until', '0')) <= now.timestamp():
                    delay = 1
                stop.wait(delay)
        finally:
            worker.db.close()
            journal.client.close()


if __name__ == '__main__':
    main()
