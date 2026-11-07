"""Private, confirmation-based Telegram input. Only outbound network connections."""
import argparse
import json
import logging
import os
import secrets
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from .parser import HELP, LABELS, parse_entry, photo_caption

logger = logging.getLogger('stride-bot')
MAX_IMAGE = 12 * 1024 * 1024


class ServiceError(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status


class Bot:
    def __init__(self, token, user_id, api_url, api_token, data_dir, client=None):
        self.token, self.user_id, self.api_url = token, int(user_id), api_url.rstrip('/')
        self.api_token = api_token
        self.client = client or httpx.Client(timeout=45, follow_redirects=False)
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(Path(data_dir) / 'telegram.sqlite3')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, update_id INTEGER UNIQUE NOT NULL,
            data TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL);
        ''')

    def stride(self, path, method='GET', payload=None, headers=None, files=None, data=None):
        response = self.client.request(method, self.api_url + '/api' + path,
            headers={'Authorization': 'Bearer ' + self.api_token, **(headers or {})}, json=payload, files=files, data=data)
        if not response.is_success:
            try:
                detail = response.json().get('detail', 'Request failed.')
                if isinstance(detail, list):
                    detail = detail[0].get('msg', 'Check the measurement values.')
            except (ValueError, AttributeError):
                detail = 'The journal server could not complete this request.'
            raise ServiceError(response.status_code, str(detail))
        return response.json() if response.content else None

    def telegram(self, method, **payload):
        response = self.client.post('https://api.telegram.org/bot' + self.token + '/' + method, json=payload)
        # Never include URLs or Telegram error bodies in logs: the URL contains a bot token.
        if not response.is_success:
            raise ServiceError(response.status_code, 'Telegram could not complete this request.')
        result = response.json()
        if not result.get('ok'):
            raise ServiceError(502, 'Telegram returned an unsuccessful response.')
        return result['result']

    def send(self, text, keyboard=None):
        payload = {'chat_id': self.user_id, 'text': text[:4000]}
        if keyboard:
            payload['reply_markup'] = keyboard
        return self.telegram('sendMessage', **payload)

    def offset(self):
        row = self.db.execute("SELECT value FROM state WHERE key='offset'").fetchone()
        return int(row['value']) if row else 0

    def set_offset(self, offset):
        self.db.execute("INSERT INTO state VALUES ('offset',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(offset),))
        self.db.commit()

    def allowed(self, user, chat):
        return user.get('id') == self.user_id and chat.get('type') == 'private' and chat.get('id') == self.user_id

    def preview_text(self, draft):
        if draft['kind'] == 'photo':
            return f"Save this photo to {draft['date']}?\n{draft['caption']}\n\nThe image will be copied from Telegram into your private journal."
        preview = draft['preview']
        lines = [f"{'Update' if preview['existing'] else 'Create'} entry for {draft['date']}?"]
        for key, value in draft['values'].items():
            if key == 'source':
                continue
            previous = preview['existing'].get(key) if preview['existing'] else None
            if key == 'notes' and previous is not None:
                previous = previous[:100] + ('…' if len(previous) > 100 else '')
            lines.append(f"{LABELS[key]}: {previous} → {value}" if previous is not None and previous != value else f"{LABELS[key]}: {value}")
        lines.append('\nOnly the fields shown above will change. Other measurements and notes are kept.')
        return '\n'.join(lines)

    def show_draft(self, identifier, draft):
        keyboard = {'inline_keyboard': [[{'text': 'Save', 'callback_data': 'save:' + identifier},
                                         {'text': 'Cancel', 'callback_data': 'cancel:' + identifier}]]}
        self.send(self.preview_text(draft), keyboard)

    def handle_message(self, update_id, message):
        if not self.allowed(message.get('from', {}), message.get('chat', {})):
            return
        text = message.get('text', '')
        if text.split(maxsplit=1)[0:1] in (['/start'], ['/help']):
            self.send(HELP)
            return
        if text == '/whoami':
            self.send(f'Your Telegram user ID: {self.user_id}')
            return
        if text == '/cancel':
            row = self.db.execute("SELECT id FROM drafts WHERE status='pending' ORDER BY created DESC LIMIT 1").fetchone()
            if row:
                self.db.execute("UPDATE drafts SET status='cancelled' WHERE id=?", (row['id'],))
                self.db.commit()
            self.send('Latest pending draft cancelled.' if row else 'There is no pending draft.')
            return
        existing = self.db.execute('SELECT * FROM drafts WHERE update_id=?', (update_id,)).fetchone()
        if existing:
            if existing['status'] == 'pending':
                self.show_draft(existing['id'], json.loads(existing['data']))
            return
        profile = self.stride('/profile')
        default_date = datetime.fromtimestamp(message.get('date', time.time()), ZoneInfo(profile['timezone'])).date().isoformat()
        try:
            if message.get('photo') or message.get('document'):
                file = max(message['photo'], key=lambda p: p.get('file_size', 0)) if message.get('photo') else message['document']
                if file.get('file_size', 0) > MAX_IMAGE:
                    raise ValueError('Choose an image smaller than 12 MB.')
                if message.get('document') and file.get('mime_type') not in ('image/jpeg', 'image/png', 'image/webp'):
                    raise ValueError('Use a JPEG, PNG or WebP image.')
                day, caption = photo_caption(message.get('caption', ''), default_date)
                if len(caption) > 1000:
                    raise ValueError('Keep photo captions under 1,000 characters.')
                draft = {'kind': 'photo', 'date': day, 'caption': caption, 'file_id': file['file_id']}
            else:
                draft = {'kind': 'entry', **parse_entry(text, default_date)}
                draft['preview'] = self.stride('/entries/preview', 'POST', {'date': draft['date'], 'values': draft['values']})
        except (ValueError, ServiceError) as e:
            self.send(f'Nothing saved: {e}\n\nUse /help for examples.')
            return
        identifier = secrets.token_hex(12)
        self.db.execute('INSERT INTO drafts (id,update_id,data,created) VALUES (?,?,?,?)',
                        (identifier, update_id, json.dumps(draft), time.time()))
        self.db.commit()  # A durable draft exists before the Telegram confirmation is sent.
        self.show_draft(identifier, draft)

    def download(self, file_id):
        info = self.telegram('getFile', file_id=file_id)
        path = info.get('file_path', '')
        if not path or path.startswith('/') or '..' in path.split('/') or info.get('file_size', 0) > MAX_IMAGE:
            raise ValueError('This file cannot be downloaded. Choose an image smaller than 12 MB.')
        content = bytearray()
        with self.client.stream('GET', 'https://api.telegram.org/file/bot' + self.token + '/' + path) as response:
            if not response.is_success:
                raise ServiceError(response.status_code, 'The image could not be downloaded from Telegram.')
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > MAX_IMAGE:
                    raise ValueError('Choose an image smaller than 12 MB.')
        return bytes(content)

    def handle_callback(self, query):
        if not self.allowed(query.get('from', {}), query.get('message', {}).get('chat', {})):
            return
        action, _, identifier = query.get('data', '').partition(':')
        if action not in ('save', 'cancel'):
            return
        try:
            self.telegram('answerCallbackQuery', callback_query_id=query['id'])
        except ServiceError as e:
            # Telegram expires callback acknowledgements quickly. An old acknowledgement
            # must not strand this update and block the durable polling offset forever.
            if e.status != 400:
                raise
        row = self.db.execute('SELECT * FROM drafts WHERE id=?', (identifier,)).fetchone()
        if not row or time.time() - row['created'] > 86400:
            self.send('This draft has expired. Send the check-in again.')
            return
        if row['status'] != 'pending':
            self.send('This draft was already ' + row['status'] + '.')
            return
        if action == 'cancel':
            self.db.execute("UPDATE drafts SET status='cancelled' WHERE id=?", (identifier,))
            self.db.commit()
            self.send('Cancelled. Nothing saved.')
            return
        draft = json.loads(row['data'])
        key = f'telegram:{self.user_id}:{identifier}'
        try:
            if draft['kind'] == 'entry':
                self.stride('/entries/by-date/' + draft['date'], 'PATCH', draft['values'],
                            headers={'Idempotency-Key': key, 'If-Match': draft['preview']['version']})
            else:
                content = self.download(draft['file_id'])
                self.stride('/photos', 'POST', headers={'Idempotency-Key': key}, files={'file': ('telegram-image', content)},
                            data={'photo_date': draft['date'], 'caption': draft['caption'], 'source': 'telegram'})
        except ServiceError as e:
            if e.status == 409 and draft['kind'] == 'entry':
                draft['preview'] = self.stride('/entries/preview', 'POST', {'date': draft['date'], 'values': draft['values']})
                self.db.execute('UPDATE drafts SET data=? WHERE id=?', (json.dumps(draft), identifier))
                self.db.commit()
                self.send('Your journal changed since the preview. Please review the updated draft.')
                self.show_draft(identifier, draft)
            else:
                self.send('Not saved. ' + ('Check your Stride API token in the server configuration, then tap Save again.' if e.status == 401 else str(e)))
            return
        except ValueError as e:
            self.send('Not saved. ' + str(e))
            return
        self.db.execute("UPDATE drafts SET status='saved' WHERE id=?", (identifier,))
        self.db.commit()  # Idempotency at the API covers a crash between writing and this commit.
        self.send(f"Saved {'photo' if draft['kind'] == 'photo' else 'entry'} for {draft['date']}." +
                  (' Open Photo journal to review landmarks or run local analysis.' if draft['kind'] == 'photo' else ''))

    def process(self, update):
        if 'callback_query' in update:
            self.handle_callback(update['callback_query'])
        elif 'message' in update:
            self.handle_message(update['update_id'], update['message'])

    def run(self):
        me = self.telegram('getMe')
        # Polling and webhooks are mutually exclusive. Preserve pending updates when switching.
        self.telegram('deleteWebhook', drop_pending_updates=False)
        logger.info('Bot started with a private user allowlist.')
        while True:
            try:
                self.stride('/integrations/telegram/heartbeat', 'POST', {'username': me.get('username', '')})
                updates = self.telegram('getUpdates', offset=self.offset(), timeout=30, allowed_updates=['message', 'callback_query'])
                for update in updates:
                    self.process(update)
                    self.set_offset(update['update_id'] + 1)
                self.db.execute('DELETE FROM drafts WHERE created<?', (time.time() - 30 * 86400,))
                self.db.commit()
            except (httpx.HTTPError, ServiceError, ValueError, KeyError):
                logger.warning('Integration request failed; retrying in 5 seconds. Check connectivity and credentials.')
                time.sleep(5)


def discover(token):
    """Read-only setup helper; prints sender IDs without sending any messages."""
    with httpx.Client(timeout=40) as client:
        response = client.post(f'https://api.telegram.org/bot{token}/getUpdates', json={'timeout': 20, 'allowed_updates': ['message']})
        if not response.is_success or not response.json().get('ok'):
            raise ValueError('Could not read updates. Check the token and remove any existing webhook.')
        ids = {u['message']['from']['id'] for u in response.json()['result'] if u.get('message', {}).get('chat', {}).get('type') == 'private'}
        print('Private sender IDs:', ', '.join(map(str, sorted(ids))) if ids else 'None yet. Send /start to your bot and try again.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--whoami', action='store_true', help='Read private sender IDs for initial setup; does not send messages.')
    args = parser.parse_args()
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not token:
        raise SystemExit('Set TELEGRAM_BOT_TOKEN in your .env file.')
    if args.whoami:
        try:
            discover(token)
        except (ValueError, httpx.HTTPError):
            raise SystemExit('Could not read Telegram updates. Check the token, connectivity and webhook configuration.')
        return
    user = os.environ.get('TELEGRAM_ALLOWED_USER_ID', '').strip()
    api_token = os.environ.get('STRIDE_API_TOKEN', '').strip()
    if not user.isdigit() or int(user) <= 0 or not api_token:
        raise SystemExit('Set a positive TELEGRAM_ALLOWED_USER_ID and a STRIDE_API_TOKEN in .env before starting the bot.')
    logging.basicConfig(level=logging.WARNING)
    logger.setLevel(logging.INFO)
    bot = Bot(token, int(user), os.environ.get('STRIDE_API_URL', 'http://stride:8000'), api_token,
              os.environ.get('BOT_DATA_DIR', './bot-data'))
    try:
        bot.run()
    except (httpx.HTTPError, ServiceError):
        raise SystemExit('Bot startup failed. Check credentials and network access; no secrets were logged.')


if __name__ == '__main__':
    main()
