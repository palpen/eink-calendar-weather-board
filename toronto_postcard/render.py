"""Native 600 x 448 Toronto postcard, using the Inky color indices directly."""

from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .model import agenda

BLACK, WHITE, GREEN, BLUE, RED, YELLOW, ORANGE = range(7)
COLORS = [(0, 0, 0), (255, 255, 255), (0, 120, 60), (0, 72, 160),
          (200, 0, 0), (255, 215, 0), (240, 110, 0)]
MAC = Path('/System/Library/Fonts/Supplemental')
LINUX = Path('/usr/share/fonts/truetype/dejavu')
FONT_FILES = {
    'sans': (MAC / 'Arial.ttf', LINUX / 'DejaVuSans.ttf'),
    'bold': (MAC / 'Arial Bold.ttf', LINUX / 'DejaVuSans-Bold.ttf'),
    'rounded': (MAC / 'Arial Rounded Bold.ttf', LINUX / 'DejaVuSans-Bold.ttf'),
    'serif': (MAC / 'Georgia Bold.ttf', LINUX / 'DejaVuSerif-Bold.ttf'),
    'mono': (MAC / 'Courier New Bold.ttf', LINUX / 'DejaVuSansMono-Bold.ttf'),
}


@lru_cache(maxsize=128)
def font(family, size):
    path = next((path for path in FONT_FILES[family] if path.is_file()), None)
    if path is None:
        raise RuntimeError('Install fonts-dejavu-core or set up the macOS system fonts')
    return ImageFont.truetype(str(path), size)


def clean(value):
    return ' '.join(str(value).replace('\x00', '').split())


def ellipsis(value, width, family='sans', size=18):
    value = clean(value)
    f = font(family, size)
    if f.getlength(value) <= width:
        return value
    low, high = 0, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if f.getlength(value[:middle] + '…') <= width:
            low = middle
        else:
            high = middle - 1
    return value[:low].rstrip() + '…'


def wrap(value, width, family='sans', size=18, limit=2):
    words, lines, current = clean(value).split(), [], ''
    for index, word in enumerate(words):
        candidate = (current + ' ' + word).strip()
        if font(family, size).getlength(candidate) <= width:
            current = candidate
        else:
            if not current:
                current = ellipsis(word, width, family, size)
                if index < len(words) - 1 and limit == 1:
                    return [ellipsis(' '.join(words), width, family, size)]
            lines.append(current)
            if len(lines) == limit - 1:
                lines.append(ellipsis(' '.join(words[index:]), width, family, size))
                return lines
            current = word
    if current:
        lines.append(current)
    return lines[:limit] or ['Untitled event']


class Canvas:
    def __init__(self):
        self.image = Image.new('P', (600, 448), WHITE)
        self.image.putpalette([channel for rgb in COLORS for channel in rgb] + [0] * (768 - 21))
        self.draw = ImageDraw.Draw(self.image)

    def text(self, x, y, value, size=18, family='sans', color=BLACK, width=None):
        value = ellipsis(value, width, family, size) if width else clean(value)
        f = font(family, size)
        box = f.getbbox(value, anchor='lt')
        if x + box[2] > 600 or y + box[3] > 448 or x < 0 or y < 0:
            raise ValueError('Text falls outside the panel')
        mask = Image.new('L', (600, 448))
        ImageDraw.Draw(mask).text((x, y), value, font=f, fill=255, anchor='lt')
        self.image.paste(color, (0, 0), mask.point(lambda alpha: 255 if alpha >= 100 else 0))

    def rect(self, x, y, width, height, color):
        self.draw.rectangle((round(x), round(y), round(x + width - 1), round(y + height - 1)), fill=color)

    def line(self, x1, y1, x2, y2, color=BLACK, width=1):
        self.draw.line((round(x1), round(y1), round(x2), round(y2)), fill=color, width=width)

    def circle(self, x, y, width, height, color, outline=None, stroke=2):
        self.draw.ellipse((round(x), round(y), round(x + width), round(y + height)),
                          fill=color, outline=outline, width=stroke)


def condition(code):
    if code == 0: return 'Clear sky'
    if code in (1, 2): return 'Partly cloudy'
    if code == 3: return 'Overcast'
    if code in (45, 48): return 'Fog'
    if code in (51, 53, 55): return 'Drizzle'
    if code in (56, 57, 66, 67): return 'Freezing rain'
    if code in (61, 63, 65): return 'Rain'
    if code in (71, 73, 75, 77): return 'Snow'
    if code in (80, 81, 82): return 'Rain showers'
    if code in (85, 86): return 'Snow showers'
    if code in (95, 96, 99): return 'Thunderstorms'
    return 'Weather unknown'


def weather_icon(c, code, is_day):
    x, y = 173, 112
    if code in (0, 1, 2):
        c.circle(x + 33, y + 3, 35, 35, YELLOW if is_day else BLUE, BLACK)
        if is_day:
            c.line(x + 50, y - 5, x + 50, y - 13, width=2)
            c.line(x + 72, y + 20, x + 79, y + 20, width=2)
            c.line(x + 66, y, x + 72, y - 6, width=2)
        else:
            c.circle(x + 24, y - 2, 33, 31, WHITE)
    if code == 0:
        return
    c.circle(x + 3, y + 35, 33, 30, WHITE, BLACK)
    c.circle(x + 20, y + 19, 40, 44, WHITE, BLACK)
    c.circle(x + 48, y + 35, 30, 29, WHITE, BLACK)
    c.rect(x + 21, y + 39, 42, 23, WHITE)
    c.line(x + 21, y + 65, x + 61, y + 65, width=2)
    if code in (45, 48):
        c.line(x + 2, y + 72, x + 76, y + 72, BLUE, 2)
        c.line(x + 8, y + 79, x + 68, y + 79, BLUE, 2)
    elif code in (71, 73, 75, 77, 85, 86):
        for dx in (18, 39, 60):
            c.line(x + dx - 3, y + 73, x + dx + 3, y + 79, BLUE, 2)
            c.line(x + dx + 3, y + 73, x + dx - 3, y + 79, BLUE, 2)
    elif code >= 51:
        for dx in (20, 42, 64):
            c.line(x + dx, y + 71, x + dx - 4, y + 80, BLUE, 2)
        if code >= 95:
            c.draw.polygon([(x + 47, y + 62), (x + 39, y + 77), (x + 48, y + 77),
                            (x + 40, y + 91), (x + 58, y + 72), (x + 49, y + 72)], fill=YELLOW)


def skyline(c):
    for x, top, width in [(0, 388, 26), (34, 379, 24), (66, 365, 25), (101, 384, 29),
                          (195, 381, 27), (230, 367, 25), (264, 392, 25)]:
        c.rect(x, top, width, 427 - top, BLUE)
        for wx in range(x + 7, x + width - 4, 9):
            for wy in range(top + 9, 412, 12):
                c.rect(wx, wy, 3, 4, WHITE)
    c.line(163, 356, 163, 431, BLUE, 5)
    c.line(163, 349, 163, 362, BLUE, 2)
    c.circle(148, 372, 30, 10, BLUE)
    c.rect(157, 380, 12, 6, BLUE)
    c.circle(-36, 411, 236, 65, GREEN)
    c.circle(163, 410, 163, 60, GREEN)
    c.rect(0, 435, 289, 13, BLUE)
    for x, y in [(101, 408), (209, 407)]:
        c.line(x, y, x, y + 22, BLACK, 2)
        c.circle(x - 9, y - 11, 19, 20, GREEN)
    c.rect(289, 0, 311, 448, WHITE)
    c.line(289, 24, 289, 424)


def degrees(value):
    return '—' if value is None else str(round(value)) + '°'


def render(now, events, weather, calendar_status='live', weather_status='live', city='Toronto'):
    c = Canvas()
    c.text(24, 25, city, 31, 'serif', BLUE, 241)
    if weather:
        temp = degrees(weather['temperature'])
        size = 95
        while size > 48 and font('rounded', size).getlength(temp) > 155:
            size -= 1
        c.text(18, 82, temp, size, 'rounded')
        weather_icon(c, weather['code'], weather['is_day'])
        c.text(24, 195, condition(weather['code']), 25, 'rounded', width=241)
        c.text(24, 233, 'High ' + degrees(weather['high']) + ' / Low ' + degrees(weather['low']), 18, width=241)
        c.text(24, 275, 'Feels ' + degrees(weather['feels_like']), 16, color=BLUE, width=132)
        c.text(168, 275, 'Rain ' + (str(round(weather['rain'])) + '%' if weather['rain'] is not None else '—'),
               16, color=GREEN, width=101)
        remaining = [(ts, temp) for ts, temp in weather['hourly'] if ts >= now.timestamp() and temp is not None]
        future = remaining[::3][:4]
        for x, (timestamp, temp) in zip((24, 88, 152, 216), future):
            hour = datetime.fromtimestamp(timestamp, now.tzinfo)
            label = hour.strftime('%H') + ('+' if hour.date() != now.date() else '')
            c.text(x, 305, label, 13, 'mono')
            c.text(x, 326, degrees(temp), 23, 'rounded', BLUE, 61)
    else:
        c.text(24, 100, 'Weather', 28, 'rounded')
        c.text(24, 144, 'unavailable', 28, 'rounded')
        c.text(24, 219, 'Trying again shortly', 18, width=241)
    skyline(c)
    if weather:
        c.text(12, 436, 'Open-Meteo', 11, color=WHITE)
    c.text(313, 25, now.strftime('%A'), 29, 'rounded', BLUE, 263)
    c.text(313, 66, f'{now.day} {now.strftime("%B %Y")}', 17, width=263)
    all_day, visible, highlighted, hidden_count = agenda(events, now)
    y = 96
    shown_all_day = all_day[:2] if len(all_day) <= 2 else all_day[:1]
    for event in shown_all_day:
        c.text(313, y, 'ALL DAY', 11, 'bold', GREEN)
        c.text(372, y - 1, event.title, 15, width=204)
        y += 20
    if len(all_day) > 2:
        c.text(313, y, f'+{len(all_day) - 1} all-day events', 12, color=GREEN)
        y += 20
    c.line(313, y + 4, 576, y + 4, BLUE, 2)
    top, available = y + 19, 411 - (y + 19)
    row_height = min(73, available // max(1, len(visible)))
    if not visible:
        if calendar_status == 'unavailable':
            message = 'Calendar unavailable'
        elif any(not event.all_day for event in events):
            message = 'No more events today'
        else:
            message = 'No events scheduled today'
        c.text(313, top + 15, message, 20, 'rounded', width=263)
        if calendar_status != 'unavailable':
            c.text(313, top + 51, 'Enjoy the open space.', 17, width=263)
    for index, event in enumerate(visible):
        y = top + index * row_height
        selected = highlighted is not None and event.uid == highlighted.uid
        c.text(325 if selected else 313, y, event.start.strftime('%H:%M') + '–' + event.end.strftime('%H:%M'),
               15, 'sans', width=180)
        if selected:
            c.rect(313, y - 2, 4, min(row_height - 6, 58), RED)
            tag = 'NOW' if event.start.timestamp() <= now.timestamp() < event.end.timestamp() else 'NEXT UP'
            c.text(517, y + 2, tag, 13, 'bold', RED, 59)
        x = 325 if selected else 313
        family = 'bold' if selected else 'sans'
        lines = wrap(event.title, 576 - x, family, 18, limit=2)
        for line_index, line in enumerate(lines):
            c.text(x, y + 21 + line_index * 20, line, 18, family, width=576 - x)
        if len(lines) == 1 and event.location and row_height >= 60:
            c.text(x, y + 43, event.location, 13, width=576 - x)
        if index < len(visible) - 1:
            c.line(313, y + row_height - 6, 576, y + row_height - 6)
    if hidden_count:
        c.text(493, 413, f'+{hidden_count} more', 12, width=83)
    warnings = []
    if calendar_status == 'snapshot': warnings.append('Calendar snapshot')
    elif calendar_status == 'unavailable': warnings.append('Calendar offline')
    if weather_status != 'live': warnings.append('Weather ' + weather_status)
    if warnings:
        c.text(313, 413, ' · '.join(warnings), 11, width=170)
    footer = 'Last refresh ' + now.strftime('%d %b %Y, %H:%M')
    c.text(576 - font('sans', 11).getlength(footer), 429, footer, 11)
    return c.image
