"""Group routing and authorization use synthetic IDs and a mocked Telegram API."""
import json
import sqlite3
import time

import pytest

from backend.tests.test_app import client
from backend.tests.test_integrations import make_bot, message
from integrations.telegram.bot import Bot, main


GROUP = -1001234567890


def group_message(update_id, text='', *, thread_id=0, **extra):
    update = message(update_id, text, **extra)
    update['message']['chat'] = {'id': GROUP, 'type': 'supergroup'}
    if thread_id:
        update['message']['message_thread_id'] = thread_id
    return update


def click(bot, identifier, *, chat_id=GROUP, thread_id=0, user_id=123, action='save'):
    bot.process({'update_id': 500, 'callback_query': {
        'id': 'synthetic-callback', 'from': {'id': user_id}, 'data': action + ':' + identifier,
        'message': {'chat': {'id': chat_id, 'type': 'private' if chat_id > 0 else 'supergroup'},
                    'message_thread_id': thread_id}}})


def latest(bot):
    return bot.db.execute('SELECT * FROM drafts ORDER BY created DESC LIMIT 1').fetchone()


def test_groups_opt_in_sender_and_chat_allowlists(client, tmp_path):
    payloads = []
    bot, sent, _ = make_bot(client, tmp_path, sent_payloads=payloads)
    bot.process(group_message(1, '/help'))
    assert not sent  # Private-only until explicitly configured.
    bot.group_id = GROUP
    cases = [group_message(2, '/help'), group_message(3, '/help'), group_message(4, '/help')]
    cases[0]['message']['from']['id'] = 999
    cases[1]['message']['chat']['id'] = GROUP - 1
    cases[2]['message']['chat']['type'] = 'channel'
    for update in cases:
        bot.process(update)
    bot.process(group_message(5, '/help@AnotherBot'))
    bot.process(group_message(6, 'Ordinary group conversation'))
    bot.process(group_message(7, 'knee: 41.2'))
    bot.process(group_message(8, '/unknown'))
    bot.process(group_message(9, photo=[{'file_id': 'unused'}]))
    assert not sent
    assert bot.db.execute('SELECT count(*) FROM drafts').fetchone()[0] == 0
    bot.process(group_message(10, '/help@StrideTestBot', thread_id=42))
    assert payloads[-1]['chat_id'] == GROUP
    assert payloads[-1]['message_thread_id'] == 42
    bot.process(group_message(11, '/whoami@StrideTestBot'))
    assert f'Chat ID: {GROUP}' in sent[-1]
    assert 'message_thread_id' not in payloads[-1]
    bot.process(message(12, '/help'))
    assert payloads[-1]['chat_id'] == 123  # Private chat still works.


def test_group_confirmation_scopes_retry_and_restart(client, tmp_path):
    payloads = []
    bot, sent, token = make_bot(client, tmp_path, group_id=GROUP, sent_payloads=payloads)
    auth = {'Authorization': 'Bearer ' + token}
    bot.process(group_message(20, '/log@StrideTestBot\n2020-01-15\nknee: 41.2', thread_id=42))
    draft = latest(bot)
    assert (draft['chat_id'], draft['thread_id']) == (GROUP, 42)
    assert not any(e['date'] == '2020-01-15' for e in client.get('/api/entries', headers=auth).json())
    for args in ({'user_id': 999}, {'chat_id': GROUP - 1}, {'chat_id': 123}, {'thread_id': 43}):
        before = len(sent)
        click(bot, draft['id'], **({'thread_id': 42} | args))
        assert len(sent) == before
        assert latest(bot)['status'] == 'pending'
    restarted = Bot('fake-token', 123, 'http://stride', token, tmp_path / 'bot', client=bot.client, group_id=GROUP)
    click(restarted, draft['id'], thread_id=42)
    assert latest(restarted)['status'] == 'saved'
    assert payloads[-1]['chat_id'] == GROUP and payloads[-1]['message_thread_id'] == 42
    assert sent[-1] == 'Saved entry for 2020-01-15.'
    click(restarted, draft['id'], thread_id=42)
    assert 'already saved' in sent[-1]
    entries = client.get('/api/entries', headers=auth).json()
    assert len([e for e in entries if e['date'] == '2020-01-15' and e['knee'] == 41.2]) == 1


def test_cancel_separates_private_chat_group_and_topics(client, tmp_path):
    bot, sent, _ = make_bot(client, tmp_path, group_id=GROUP)
    bot.process(message(30, '/log\n2020-01-15\nknee: 41.2'))
    private = latest(bot)['id']
    bot.process(group_message(31, '/log\n2020-01-16\nknee: 41.1', thread_id=42))
    topic = latest(bot)['id']
    bot.process(group_message(32, '/log\n2020-01-17\nknee: 41.0'))
    general = latest(bot)['id']
    bot.process(group_message(33, '/cancel@StrideTestBot', thread_id=43))
    assert sent[-1] == 'There is no pending draft.'
    bot.process(group_message(34, '/cancel@StrideTestBot', thread_id=42))
    statuses = {row['id']: row['status'] for row in bot.db.execute('SELECT * FROM drafts')}
    assert statuses == {private: 'pending', topic: 'cancelled', general: 'pending'}
    click(bot, general, action='cancel')
    assert latest(bot)['status'] == 'cancelled'


def test_group_replies_photos_and_conflict_preview_stay_in_topic(client, tmp_path):
    payloads = []
    bot, sent, token = make_bot(client, tmp_path, group_id=GROUP, sent_payloads=payloads)
    auth = {'Authorization': 'Bearer ' + token}
    reply = {'from': {'id': bot.bot_id}}
    bot.process(group_message(40, '2020-01-15\nknee: 41.2', thread_id=42, reply_to_message=reply))
    draft = latest(bot)
    client.patch('/api/entries/by-date/2020-01-15', json={'knee': 42}, headers=auth)
    click(bot, draft['id'], thread_id=42)
    assert '42.0 → 41.2' in sent[-1]
    assert all(p['chat_id'] == GROUP and p['message_thread_id'] == 42 for p in payloads)
    click(bot, draft['id'], thread_id=42)
    bot.process(group_message(41, thread_id=42, photo=[{'file_id': 'synthetic-image', 'file_size': 100}],
                              caption='2020-01-15\nExample', reply_to_message=reply))
    photo = latest(bot)
    assert json.loads(photo['data'])['caption'] == 'Example'
    assert client.get('/api/photos', headers=auth).json() == []
    click(bot, photo['id'], thread_id=42)
    assert len(client.get('/api/photos', headers=auth).json()) == 1
    assert payloads[-1]['chat_id'] == GROUP and payloads[-1]['message_thread_id'] == 42
    bot.process(group_message(42, photo=[{'file_id': 'synthetic-image', 'file_size': 100}],
                              caption='/log@StrideTestBot\n2020-01-16\nExample caption'))
    assert json.loads(latest(bot)['data'])['date'] == '2020-01-16'
    assert json.loads(latest(bot)['data'])['caption'] == 'Example caption'


def test_upgrade_preserves_legacy_private_drafts(tmp_path):
    directory = tmp_path / 'bot'
    directory.mkdir()
    with sqlite3.connect(directory / 'telegram.sqlite3') as db:
        db.execute('CREATE TABLE drafts (id TEXT PRIMARY KEY, update_id INTEGER UNIQUE NOT NULL, data TEXT NOT NULL, status TEXT NOT NULL DEFAULT \'pending\', created REAL NOT NULL)')
        db.execute('INSERT INTO drafts (id,update_id,data,created) VALUES (?,?,?,?)', ('legacy', 1, '{}', time.time()))
    bot = Bot('fake-token', 123, 'http://stride', 'fake-api-token', directory, group_id=GROUP)
    assert (latest(bot)['chat_id'], latest(bot)['thread_id'], latest(bot)['status']) == (123, 0, 'pending')
    bot.db.close()
    bot.client.close()
    restarted = Bot('fake-token', 123, 'http://stride', 'fake-api-token', directory, group_id=GROUP)
    assert latest(restarted)['chat_id'] == 123
    restarted.db.close()
    restarted.client.close()


@pytest.mark.parametrize('value', ['123', '-0', 'group-name', '-123,456'])
def test_rejects_invalid_group_config(monkeypatch, value):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'fake-token')
    monkeypatch.setenv('TELEGRAM_ALLOWED_GROUP_ID', value)
    monkeypatch.setattr('sys.argv', ['bot'])
    with pytest.raises(SystemExit, match='negative numeric group ID'):
        main()
