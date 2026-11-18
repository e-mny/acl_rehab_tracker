"""Synthetic charts and mocked Telegram uploads; no messages leave the tests."""
from datetime import date, datetime, timezone
from email.parser import BytesParser
from email.policy import default
from io import BytesIO
import json
import math

import httpx
from PIL import Image
import pytest

from integrations.telegram.bot import Bot
from integrations.telegram.graphs import GraphRequest, measurement_figure, parse_graph_args, render_graph


ENTRIES = [
    {'date': '2020-01-15', 'knee': 41.2, 'above5': 43.7, 'above15': 53.6,
     'other_knee': 40.0, 'other_above5': 42.9, 'other_above15': 52.7,
     'flexion': 90, 'extension': 4, 'pain_rest': 0, 'pain_activity': 3},
    {'date': '2020-01-16', 'knee': None, 'above5': 43.5, 'pain_rest': 2},
    {'date': '2020-01-17', 'knee': 41.0, 'above5': 43.3, 'above15': 53.4,
     'flexion': 100, 'extension': -2, 'pain_rest': 0, 'pain_activity': 2},
]
GROUP = -1001234567890


@pytest.mark.parametrize(('text', 'expected'), [
    ('', GraphRequest()), ('all', GraphRequest()), ('30', GraphRequest(days=30)),
    ('motion 90', GraphRequest('motion', 90)), ('7 pain', GraphRequest('pain', 7)),
    ('CIRCUMFERENCE 365', GraphRequest('circumference', 365)),
])
def test_graph_arguments(text, expected):
    assert parse_graph_args(text) == expected


@pytest.mark.parametrize('text', ['0', '366', '-1', '1.5', 'weekly', '30 90', 'all all', 'pain motion', 'pain 30 extra'])
def test_invalid_graph_arguments(text):
    with pytest.raises(ValueError, match='Use /graph'):
        parse_graph_args(text)


def test_chart_preserves_dates_missing_values_and_actual_comparison_dates():
    figure, caption = measurement_figure(list(reversed(ENTRIES)), GraphRequest())
    try:
        assert len(figure.axes) == 3
        recovering, comparison = figure.axes[0].lines
        assert list(recovering.get_xdata()) == [date(2020, 1, day) for day in (15, 16, 17)]
        assert recovering.get_ydata()[0] == 41.2 and math.isnan(recovering.get_ydata()[1])
        assert comparison.get_ydata()[0] == 40
        assert all(math.isnan(value) for value in comparison.get_ydata()[1:])
        assert '2020-01-15 to 2020-01-17' in caption
    finally:
        figure.clear()


def test_chart_range_is_inclusive_and_excludes_future_dates():
    entries = [{'date': day, 'knee': value} for day, value in [
        ('2020-01-01', 99), ('2020-01-02', 41.2), ('2020-01-31', 41.0), ('2020-02-01', 98)]]
    figure, caption = measurement_figure(entries, GraphRequest(days=30), as_of=date(2020, 1, 31))
    try:
        assert list(figure.axes[0].lines[0].get_ydata()) == [41.2, 41.0]
        assert '2020-01-02 to 2020-01-31' in caption
        assert 'last 30 days, through 2020-01-31' in caption
    finally:
        figure.clear()


def test_zero_pain_and_signed_extension_are_not_lost():
    figure, _ = measurement_figure(ENTRIES, GraphRequest('pain'))
    try:
        assert list(figure.axes[0].lines[0].get_ydata()) == [0, 2, 0]
        assert figure.axes[0].get_ylim() == (-.5, 10.5)
    finally:
        figure.clear()
    figure, caption = measurement_figure(ENTRIES, GraphRequest('motion'))
    try:
        values = figure.axes[1].lines[0].get_ydata()
        assert values[0] == 4 and values[-1] == -2
        assert 'negative = hyperextension' in caption
    finally:
        figure.clear()


@pytest.mark.parametrize('entries', [[], [{'date': '2020-01-15', 'notes': 'Example note'}]])
def test_no_measurements_is_a_readable_error(entries):
    with pytest.raises(ValueError, match='No circumference measurements recorded'):
        render_graph(entries, GraphRequest())


def test_one_point_png_meets_telegram_limits_and_excludes_notes():
    png, caption = render_graph([ENTRIES[0] | {'notes': 'PRIVATE NOTE', 'name': 'PRIVATE NAME'}], GraphRequest())
    with Image.open(BytesIO(png)) as image:
        assert image.format == 'PNG'
        width, height = image.size
        assert width >= 1000 and height >= 1000
        assert width + height <= 10000 and max(width / height, height / width) <= 20
        image.verify()
    assert len(png) < 10 * 1024 * 1024
    assert b'PRIVATE NOTE' not in png and b'PRIVATE NAME' not in png
    assert 'PRIVATE' not in caption


@pytest.fixture
def graph_bot(tmp_path):
    requests = []
    state = {'entries': ENTRIES, 'api_status': 200, 'photo_status': 200}
    def route(request):
        requests.append(request)
        if request.url.host == 'stride':
            assert request.method == 'GET' and request.headers['Authorization'] == 'Bearer fake-api-token'
            body = {'timezone': 'Asia/Singapore'} if request.url.path.endswith('/profile') else state['entries']
            return httpx.Response(state['api_status'], json=body)
        status = state['photo_status'] if request.url.path.endswith('/sendPhoto') else 200
        return httpx.Response(status, json={'ok': status == 200, 'result': {}})
    http = httpx.Client(transport=httpx.MockTransport(route))
    bot = Bot('fake-bot-token', 123, 'http://stride', 'fake-api-token', tmp_path, client=http, group_id=GROUP)
    bot.username, bot.bot_id = 'StrideTestBot', 456
    yield bot, requests, state
    bot.db.close()
    http.close()


def message(text, *, chat_id=123, user_id=123, thread_id=0):
    return {'update_id': 1, 'message': {'from': {'id': user_id},
            'chat': {'id': chat_id, 'type': 'private' if chat_id > 0 else 'supergroup'},
            'message_thread_id': thread_id, 'text': text,
            # This is Jan 17 in the journal timezone, but Jan 16 in UTC.
            'date': datetime(2020, 1, 16, 17, tzinfo=timezone.utc).timestamp()}}


def photo_parts(request):
    message = BytesParser(policy=default).parsebytes(
        b'Content-Type: ' + request.headers['content-type'].encode() + b'\r\nMIME-Version: 1.0\r\n\r\n' + request.content)
    return {part.get_param('name', header='content-disposition'): part.get_payload(decode=True)
            for part in message.iter_parts()}


def test_private_graph_uploads_real_png_without_writing_journal_or_draft(graph_bot):
    bot, requests, _ = graph_bot
    bot.process(message('/graph'))
    photos = [r for r in requests if r.url.path.endswith('/sendPhoto')]
    assert len(photos) == 1
    parts = photo_parts(photos[0])
    assert parts['chat_id'] == b'123' and 'message_thread_id' not in parts
    assert parts['photo'].startswith(b'\x89PNG\r\n\x1a\n')
    assert b'Circumference measurements' in parts['caption']
    assert bot.db.execute('SELECT count(*) FROM drafts').fetchone()[0] == 0
    assert all(r.method == 'GET' for r in requests if r.url.host == 'stride')


def test_group_graph_stays_in_topic_and_filters_using_journal_timezone(graph_bot):
    bot, requests, _ = graph_bot
    bot.process(message('/graph@StrideTestBot motion 1', chat_id=GROUP, thread_id=42))
    parts = photo_parts(next(r for r in requests if r.url.path.endswith('/sendPhoto')))
    assert parts['chat_id'] == str(GROUP).encode() and parts['message_thread_id'] == b'42'
    assert b'2020-01-17 to 2020-01-17' in parts['caption']
    assert b'Range of motion' in parts['caption']


@pytest.mark.parametrize('update', [message('/graph', user_id=999), message('/graph', chat_id=GROUP - 1),
                                   message('/graph@OtherBot', chat_id=GROUP)])
def test_graph_access_denied_without_fetching_or_sending(update, graph_bot):
    bot, requests, _ = graph_bot
    bot.process(update)
    assert not requests


@pytest.mark.parametrize(('command', 'setting', 'expected'), [
    ('/graph 0', {}, 'Use /graph'),
    ('/graph', {'entries': []}, 'No circumference measurements'),
    ('/graph 1', {'entries': [ENTRIES[0]]}, 'last 1 day'),
    ('/graph', {'api_status': 401}, 'Check the journal connection'),
    ('/graph', {'photo_status': 400}, 'Check the journal connection'),
])
def test_graph_errors_reply_in_originating_topic(command, setting, expected, graph_bot):
    bot, requests, state = graph_bot
    state.update(setting)
    bot.process(message(command, chat_id=GROUP, thread_id=42))
    replies = [json.loads(r.content) for r in requests if r.url.path.endswith('/sendMessage')]
    assert expected in replies[-1]['text']
    assert replies[-1]['chat_id'] == GROUP and replies[-1]['message_thread_id'] == 42
    if command == '/graph 0':
        assert not any(r.url.host == 'stride' for r in requests)
