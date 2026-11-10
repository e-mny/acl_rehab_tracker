"""Deterministic parsing of a single daily check-in; no external AI service."""
import re
from datetime import datetime

ALIASES = {
    'knee': 'knee', 'kneecap': 'knee', 'just above kneecap': 'knee', 'above kneecap': 'knee',
    '+5': 'above5', 'above5': 'above5', '5cm above kneecap': 'above5', '5 cm above kneecap': 'above5',
    '+15': 'above15', 'above15': 'above15', '15cm above kneecap': 'above15', '15 cm above kneecap': 'above15',
    'flexion': 'flexion', 'extension': 'extension', 'extension deficit': 'extension',
    'pain': 'pain_rest', 'pain rest': 'pain_rest', 'pain at rest': 'pain_rest', 'pain_rest': 'pain_rest',
    'pain activity': 'pain_activity', 'pain with activity': 'pain_activity', 'pain_activity': 'pain_activity',
    'stiffness': 'stiffness', 'exercise': 'exercise', 'rehab': 'exercise', 'steps': 'steps',
    'sleep': 'sleep', 'instability': 'instability', 'giving way': 'instability',
    'other_knee': 'other_knee', 'other_above5': 'other_above5', 'other_above15': 'other_above15',
}
LABELS = {'knee': 'Just above kneecap (cm)', 'above5': '5 cm above (cm)', 'above15': '15 cm above (cm)',
          'other_knee': 'Other leg, above kneecap (cm)', 'other_above5': 'Other leg, +5 cm (cm)',
          'other_above15': 'Other leg, +15 cm (cm)', 'flexion': 'Flexion (°)', 'extension': 'Extension deficit (°)',
          'pain_rest': 'Pain at rest (/10)', 'pain_activity': 'Pain with activity (/10)', 'stiffness': 'Stiffness (min)',
          'exercise': 'Exercises (min)', 'steps': 'Steps', 'sleep': 'Sleep (hours)', 'instability': 'Giving-way episodes', 'notes': 'Notes'}
UNITS = {'knee': 'cm', 'above5': 'cm', 'above15': 'cm', 'flexion': '°|deg|degrees', 'extension': '°|deg|degrees',
         'pain_rest': '/10', 'pain_activity': '/10', 'stiffness': 'min|minutes', 'exercise': 'min|minutes',
         'steps': 'steps', 'sleep': 'h|hrs|hours', 'instability': 'times|episodes'}
INTEGERS = {'stiffness', 'exercise', 'steps', 'instability'}
HELP = ('Send one day at a time, for example:\n/log\nknee: 41.2\n+5: 43.7\n+15: 53.6\n'
        'pain: 2\nnotes: Morning check-in\n\nYour existing bad side / good side format also works. '
        'Other optional fields: flexion, extension, pain_activity, stiffness, exercise, steps, sleep, instability. '
        'Pain means pain at rest. Dates use day/month/year; omit the date to use the message date in your journal time zone. '
        'Values must use cm, degrees, minutes or hours as labelled. Send a JPEG/PNG/WebP photo with an optional date on the first caption line. '
        'Every entry and photo needs Save confirmation. /cancel cancels your latest pending draft in this chat/topic. '
        '/whoami shows your user and chat IDs. In groups, use /log@BotUsername followed by your measurements, '
        'or reply to a message from this bot with measurements or a photo. Replace BotUsername with this bot\'s username. '
        'Group previews and replies are visible to group members; only the allowed user can save.')


def parse_date(text):
    text = text.strip()
    formats = ('%Y-%m-%d', '%d/%m/%Y', '%d/%m/%y')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})', text):
        return None
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError('That date is not valid. Use day/month/year or YYYY-MM-DD.')


def parse_entry(text, default_date):
    day, values, side, notes_mode, date_seen = default_date, {}, 'bad', False, False
    bare_index = {'bad': 0, 'good': 0}
    for raw in text.removeprefix('/log').strip().splitlines():
        line = raw.strip()
        if not line:
            continue
        parsed_date = parse_date(line)
        if parsed_date:
            if date_seen:
                raise ValueError('Send only one date per message.')
            day, date_seen, notes_mode = parsed_date, True, False
            continue
        normalized = line.lower().replace('knee cap', 'kneecap').strip().rstrip(':').strip()
        if normalized in ('bad side', 'recovering leg', 'recovering', 'good side', 'other leg'):
            side = 'good' if normalized in ('good side', 'other leg') else 'bad'
            notes_mode = False
            continue
        if ':' in line:
            label, value = line.split(':', 1)
            label = label.lower().replace('knee cap', 'kneecap').strip()
            if label in ('date', 'entry date'):
                if date_seen:
                    raise ValueError('Send only one date per message.')
                day = parse_date(value)
                if not day:
                    raise ValueError('Use day/month/year or YYYY-MM-DD for the date.')
                date_seen, notes_mode = True, False
                continue
            if label in ('notes', 'note'):
                if 'notes' in values:
                    raise ValueError('Notes were included twice.')
                values['notes'], notes_mode = value.strip(), True
                continue
            if notes_mode and label not in ALIASES:
                values['notes'] += '\n' + line
                continue
            key = ALIASES.get(label)
            if key is None:
                raise ValueError(f'Unrecognized field: {label}. Use /help for the accepted labels.')
        elif notes_mode:
            values['notes'] += '\n' + line
            continue
        elif re.fullmatch(r'\d+(?:\.\d+)?\s*(?:cm)?', line, re.I):
            if bare_index[side] >= 3:
                raise ValueError('Only three unlabelled circumference measurements are supported per leg.')
            key = ('knee', 'above5', 'above15')[bare_index[side]]
            bare_index[side] += 1
            value = line
        else:
            raise ValueError('Use one labelled measurement per line, or notes: followed by your note.')
        notes_mode = False
        if side == 'good' and key not in ('knee', 'above5', 'above15', 'other_knee', 'other_above5', 'other_above15'):
            raise ValueError('Only circumference measurements are supported for the other leg. Add a bad side: line before symptoms or motion measurements.')
        if side == 'good' and key in ('knee', 'above5', 'above15'):
            key = 'other_' + key
        unit_key = key.removeprefix('other_')
        match = re.fullmatch(rf'\s*([+-]?\d+(?:\.\d+)?)\s*(?:{UNITS[unit_key]})?\s*', value, re.I)
        if not match:
            raise ValueError(f'Invalid value for {LABELS[key]}. Use a number with a decimal point if needed.')
        number = float(match.group(1))
        if key in INTEGERS and not number.is_integer():
            raise ValueError(f'{LABELS[key]} must be a whole number.')
        if key in values:
            raise ValueError(f'{LABELS[key]} was included twice.')
        values[key] = int(number) if key in INTEGERS else number
    if not values:
        raise ValueError('Include at least one measurement or notes: text.')
    if len(values.get('notes', '')) > 2000:
        raise ValueError('Keep Telegram notes under 2,000 characters so the full new note fits in the preview.')
    values['source'] = 'telegram'
    return {'date': day, 'values': values}


def photo_caption(text, default_date):
    lines = text.strip().splitlines()
    if lines:
        first = lines[0].removeprefix('date:').strip()
        parsed = parse_date(first)
        if parsed:
            return parsed, '\n'.join(lines[1:]).strip()
    return default_date, text.strip()
