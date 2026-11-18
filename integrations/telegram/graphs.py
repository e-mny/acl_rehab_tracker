"""Render measurement charts in memory using Matplotlib's headless canvas."""
from dataclasses import dataclass
from datetime import date, timedelta
from io import BytesIO
import math
import os
import tempfile


GRAPH_HELP = ('Use /graph [circumference|motion|pain] [1–365|all]. '
              'Examples: /graph, /graph 30, /graph motion 90, /graph pain.')
CHARTS = {
    'circumference': ('Circumference measurements', 'cm', (
        ('knee', 'Just above kneecap', 'other_knee'),
        ('above5', '5 cm above kneecap', 'other_above5'),
        ('above15', '15 cm above kneecap', 'other_above15'),
    )),
    'motion': ('Range of motion', 'degrees', (
        ('flexion', 'Flexion', None),
        ('extension', 'Extension deficit (+) / hyperextension (−)', None),
    )),
    'pain': ('Pain scores', '/10', (
        ('pain_rest', 'Pain at rest', None),
        ('pain_activity', 'Pain with activity', None),
    )),
}


@dataclass(frozen=True)
class GraphRequest:
    kind: str = 'circumference'
    days: int | None = None


def parse_graph_args(text):
    kind, days = None, None
    range_seen = False
    for token in text.lower().split():
        if token in CHARTS and kind is None:
            kind = token
        elif not range_seen and (token == 'all' or token.isascii() and token.isdecimal() and 1 <= int(token) <= 365):
            days = None if token == 'all' else int(token)
            range_seen = True
        else:
            raise ValueError(GRAPH_HELP)
    return GraphRequest(kind or 'circumference', days)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def measurement_figure(entries, request, *, as_of=None):
    """Return a figure and caption; missing values stay gaps, including zero scores."""
    title, unit, metrics = CHARTS[request.kind]
    rows = sorted(entries, key=lambda entry: entry['date'])
    if request.days is not None:
        end = as_of or date.today()
        start = end - timedelta(days=request.days - 1)
        rows = [entry for entry in rows if start <= date.fromisoformat(entry['date']) <= end]
    keys = [key for metric, _, other in metrics for key in (metric, other) if key]
    measured = [entry for entry in rows if any(_number(entry.get(key)) for key in keys)]
    if not measured:
        window = f' in the last {request.days} {"day" if request.days == 1 else "days"}' if request.days else ''
        raise ValueError(f'No {request.kind} measurements recorded{window}. Log a measurement and try /graph again.')
    first, last = measured[0]['date'], measured[-1]['date']
    rows = [entry for entry in rows if first <= entry['date'] <= last]
    dates = [date.fromisoformat(entry['date']) for entry in rows]

    # The worker has a read-only root filesystem. Cache fonts in writable /tmp;
    # chart bytes themselves are never written to disk.
    os.environ.setdefault('MPLCONFIGDIR', os.path.join(tempfile.gettempdir(), 'stride-matplotlib'))
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D

    figure = Figure(figsize=(10, 3 * len(metrics) + 1.6), dpi=140, facecolor='#f4f7f6')
    FigureCanvasAgg(figure)
    axes = figure.subplots(len(metrics), 1, sharex=True, squeeze=False).ravel()
    figure.subplots_adjust(left=.11, right=.95, top=.81, bottom=.13, hspace=.48)
    figure.text(.11, .96, 'STRIDE  /  REHAB JOURNAL', fontsize=10, color='#536b64', weight='bold')
    figure.text(.11, .92, title, fontsize=21, color='#18382e', weight='bold')
    interval = f'{first} to {last}'
    count_label = f'{len(measured)} recorded {"day" if len(measured) == 1 else "days"}'
    figure.text(.11, .885, f'{interval}  ·  {count_label}', fontsize=11, color='#536b64')
    has_comparison = any(_number(entry.get(other)) for _, _, other in metrics if other for entry in rows)
    for axis, (key, label, other) in zip(axes, metrics):
        axis.set_facecolor('white')
        axis.set_title(label, loc='left', fontsize=12, weight='bold', pad=12, color='#18382e')
        axis.set_ylabel(unit, fontsize=10, color='#536b64')
        axis.grid(axis='y', color='#e1e9e5', linewidth=.8)
        for spine in axis.spines.values():
            spine.set_visible(False)
        axis.tick_params(colors='#536b64', labelsize=10, length=0, pad=6)
        for metric, color, style in ((key, '#327c66', '-'), (other, '#638bb5', '--')):
            if metric and any(_number(entry.get(metric)) for entry in rows):
                values = [entry[metric] if _number(entry.get(metric)) else math.nan for entry in rows]
                axis.plot(dates, values, color=color, linestyle=style, linewidth=2,
                          marker='o', markersize=4, zorder=3)
        if not any(_number(entry.get(key)) or other and _number(entry.get(other)) for entry in rows):
            axis.text(.5, .5, 'No measurements recorded', transform=axis.transAxes,
                      ha='center', va='center', fontsize=11, color='#536b64')
        if key == 'extension':
            axis.axhline(0, color='#9baea5', linewidth=1, linestyle=':', zorder=1)
        if request.kind == 'pain':
            axis.set_ylim(-.5, 10.5)
            axis.set_yticks([0, 2, 4, 6, 8, 10])
        else:
            axis.margins(y=.2)
        locator = AutoDateLocator(minticks=3, maxticks=7)
        axis.xaxis.set_major_locator(locator)
        axis.xaxis.set_major_formatter(ConciseDateFormatter(locator))
    if first == last:
        axes[-1].set_xlim(dates[0] - timedelta(days=1), dates[0] + timedelta(days=1))
    axes[-1].set_xlabel('Measurement date', fontsize=10, color='#536b64', labelpad=10)
    if has_comparison:
        figure.legend(handles=[Line2D([], [], color='#327c66', marker='o', label='Recovering leg'),
                               Line2D([], [], color='#638bb5', linestyle='--', marker='o', label='Comparison leg')],
                      loc='lower center', bbox_to_anchor=(.5, .045), ncol=2, frameon=False, fontsize=10)
    figure.text(.11, .025, 'Recorded values only. Missing readings are not filled in.', fontsize=9, color='#536b64')
    caption = f'{title}\n{interval} · {count_label}'
    if request.days:
        caption += f'\nWindow: last {request.days} {"day" if request.days == 1 else "days"}, through {(as_of or date.today()).isoformat()}.'
    if request.kind == 'motion':
        caption += '\nExtension: positive = deficit; negative = hyperextension; 0 = straight.'
    return figure, caption


def render_graph(entries, request, *, as_of=None):
    figure, caption = measurement_figure(entries, request, as_of=as_of)
    try:
        output = BytesIO()
        figure.canvas.print_png(output)
        return output.getvalue(), caption
    finally:
        figure.clear()
