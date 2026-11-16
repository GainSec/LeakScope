"""Evidence images drawn from saved banners and protocol exchanges.

The image is a labelled document of bytes LeakScope already holds. It is not a
screenshot and not a terminal session: nothing is replayed or re-contacted, and
the image names the operation that produced the bytes and what, if anything,
was sent. The same facts are embedded in the PNG as text.
"""
import json
from datetime import datetime, timezone as dt_timezone
from io import BytesIO
from pathlib import Path

RENDERER = 'leakscope-render/1'
EVIDENCE_KIND = 'evidence-render'
CLASS_LABEL = 'Rendered protocol evidence'
WIDTH = 1200
MAX_HEIGHT = 4200
POSTER_HEIGHT = 750
TEXT_LINES = 40
TEXT_COLUMNS = 108
HEX_BYTES = 384
FIELD_ROWS = 24
STATES = {
    'confirmed': ('Live protocol confirmed', (74, 201, 110)),
    'reported': ('Provider reported · not verified live', (250, 178, 25)),
    'unidentified': ('Response saved · protocol not identified', (146, 160, 184)),
    'partial': ('Partial exchange · not verified', (236, 131, 90)),
    'failed': ('Failed attempt · not verified', (240, 106, 106)),
}
_INK, _MUTED, _FAINT = (230, 237, 247), (146, 160, 184), (96, 110, 134)
_PAPER, _PANEL, _RULE, _ACCENT = (10, 15, 26), (17, 26, 43), (36, 49, 74), (94, 234, 212)
_FONTS = Path(__file__).with_name('fonts')
_cache = {}


def _font(name, size):
    from PIL import ImageFont
    key = (name, size)
    if key not in _cache:
        _cache[key] = ImageFont.truetype(str(_FONTS / name), size)
    return _cache[key]


def stamp(value):
    """One unambiguous UTC form for every time printed on an image."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return value[:40]
    if not isinstance(value, datetime):
        return 'Not recorded'
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_timezone.utc)
    return value.astimezone(dt_timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def printable(text):
    """Show every character: controls become a dot, non-Latin text an escape."""
    out = []
    for char in text:
        code = ord(char)
        if char == '\t':
            out.append('    ')
        elif 32 <= code < 127 or 160 < code < 256:
            out.append(char)
        elif code < 32 or code == 127 or code == 160 or code == 0xfffd:
            out.append('·')
        else:
            out.append('\\u{%x}' % code)
    return ''.join(out)


def text_lines(data, columns=TEXT_COLUMNS, limit=TEXT_LINES):
    """Wrapped display lines of a response and how many lines were left out."""
    text = data.decode('utf-8', errors='replace') if isinstance(data, (bytes, bytearray)) else str(data)
    lines = []
    for raw in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        shown = printable(raw)
        lines.extend([shown[start:start + columns] for start in range(0, len(shown), columns)] or [''])
    while lines and not lines[-1]:
        lines.pop()
    return lines[:limit], max(0, len(lines) - limit)


def hex_lines(data, limit=HEX_BYTES):
    """Classic offset / hex / ASCII rows, and how many bytes were left out."""
    data = bytes(data)
    rows = []
    for offset in range(0, min(len(data), limit), 16):
        chunk = data[offset:offset + 16]
        left = ' '.join('%02x' % byte for byte in chunk[:8])
        right = ' '.join('%02x' % byte for byte in chunk[8:])
        ascii_text = ''.join(chr(byte) if 32 <= byte < 127 else '.' for byte in chunk)
        rows.append('%04x  %-23s  %-23s  %s' % (offset, left, right, ascii_text))
    return rows, max(0, len(data) - limit)


def field_rows(fields, limit=FIELD_ROWS):
    rows = []
    def visit(prefix, value, depth=0):
        if len(rows) > limit:
            return
        if isinstance(value, dict) and depth < 2:
            for key, item in list(value.items())[:limit]:
                visit((prefix + ' · ' if prefix else '') + str(key).replace('_', ' '), item, depth + 1)
        elif isinstance(value, (list, tuple)) and depth < 2 and len(value) <= 8 and all(not isinstance(item, (dict, list)) for item in value):
            rows.append((prefix, ', '.join(printable(str(item)) for item in value)[:160]))
        elif value is not None and value != '':
            text = json.dumps(value, ensure_ascii=True, default=str) if isinstance(value, (dict, list, tuple)) else str(value)
            rows.append((prefix, printable(text)[:160]))
    visit('', fields)
    return rows[:limit], max(0, len(rows) - limit)


def describe(document):
    """Plain-text form of the image, used as its alternative text and PNG text."""
    state = STATES.get(document.get('state'), STATES['unidentified'])[0]
    parts = [CLASS_LABEL + ' (not a screenshot).', 'Target ' + document['target'] + ' ' + document.get('transport', 'tcp') + '.',
             document.get('headline', ''), state + '.']
    parts += [label + ': ' + value + '.' for label, value in document.get('facts', [])]
    for section in document.get('sections', []):
        if section['kind'] == 'text':
            parts.append(section['title'] + ': ' + ' / '.join(text_lines(section['body'], limit=6)[0]))
        elif section['kind'] == 'fields':
            parts.append(section['title'] + ': ' + '; '.join(key + ' ' + value for key, value in field_rows(section['body'], 8)[0]))
    return ' '.join(part for part in parts if part)[:2000]


def render_document(document):
    """Draw one evidence document and return PNG bytes."""
    from PIL import Image, ImageDraw
    from PIL.PngImagePlugin import PngInfo
    sans, bold, mono, mono_bold = 'B612-Regular.ttf', 'B612-Bold.ttf', 'IBMPlexMono-Regular.ttf', 'IBMPlexMono-Bold.ttf'
    canvas = Image.new('RGB', (WIDTH, MAX_HEIGHT), _PAPER)
    draw = ImageDraw.Draw(canvas)
    left, right = 56, WIDTH - 48
    label, colour = STATES.get(document.get('state'), STATES['unidentified'])

    def wrap(text, font, width):
        lines, line = [], ''
        for word in str(text).split():
            trial = (line + ' ' + word).strip()
            if draw.textlength(trial, font=font) <= width or not line:
                line = trial
            else:
                lines.append(line); line = word
            while draw.textlength(line, font=font) > width and len(line) > 8:
                cut = len(line) - 1
                while cut > 8 and draw.textlength(line[:cut], font=font) > width:
                    cut -= 1
                lines.append(line[:cut]); line = line[cut:]
        return lines + ([line] if line else [])

    # Class band: says what kind of image this is before anything else.
    draw.rectangle((0, 0, WIDTH, 60), fill=_PANEL)
    draw.text((left, 19), CLASS_LABEL, font=_font(bold, 20), fill=_INK)
    note = 'Drawn by LeakScope from saved bytes · not a screenshot'
    draw.text((right - draw.textlength(note, font=_font(sans, 15)), 22), note, font=_font(sans, 15), fill=_MUTED)

    y = 92
    target = document['target']
    size = 40 if len(target) <= 34 else 28 if len(target) <= 52 else 20
    draw.text((left, y), target, font=_font(mono_bold, size), fill=_INK)
    end = left + draw.textlength(target, font=_font(mono_bold, size))
    draw.text((end + 14, y + size - 22), document.get('transport', 'tcp'), font=_font(mono, 17), fill=_MUTED)
    y += size + 18
    for line in wrap(document.get('headline', ''), _font(sans, 22), right - left)[:2]:
        draw.text((left, y), line, font=_font(sans, 22), fill=_INK); y += 30
    y += 8
    chip = _font(bold, 16)
    width = draw.textlength(label, font=chip)
    draw.rounded_rectangle((left, y, left + width + 44, y + 34), radius=17, outline=colour, width=2)
    draw.ellipse((left + 14, y + 12, left + 24, y + 22), fill=colour if document.get('state') == 'confirmed' else None, outline=colour, width=2)
    draw.text((left + 32, y + 7), label, font=chip, fill=colour)
    y += 58

    # Facts: where the bytes came from, when, and what was done to get them.
    key_font, value_font = _font(sans, 15), _font(sans, 17)
    for name, value in document.get('facts', [])[:10]:
        draw.text((left, y + 2), name, font=key_font, fill=_MUTED)
        for line in wrap(value, value_font, right - left - 210)[:3]:
            draw.text((left + 210, y), line, font=value_font, fill=_INK); y += 25
        y += 5
    y += 12

    body, small = _font(mono, 16), _font(sans, 14)
    for section in document.get('sections', []):
        if y > MAX_HEIGHT - 260:
            break
        draw.line((left, y + 11, right, y + 11), fill=_RULE, width=1)
        title = section['title']
        draw.rectangle((left, y, left + draw.textlength(title, font=_font(bold, 16)) + 12, y + 22), fill=_PAPER)
        draw.text((left, y), title, font=_font(bold, 16), fill=_INK)
        caption = section.get('caption', '')
        if caption:
            width = draw.textlength(caption, font=small)
            draw.rectangle((right - width - 12, y, right, y + 22), fill=_PAPER)
            draw.text((right - width, y + 2), caption, font=small, fill=_MUTED)
        y += 34
        if section['kind'] == 'fields':
            rows, omitted = field_rows(section['body'])
            for key, value in rows:
                draw.text((left, y), key[:34], font=_font(sans, 15), fill=_MUTED)
                draw.text((left + 300, y), value[:84], font=body, fill=_INK); y += 24
            lines = []
        elif section['kind'] == 'hex':
            lines, omitted = hex_lines(section['body'])
        else:
            lines, omitted = text_lines(section['body'])
        if section['kind'] != 'fields':
            if not lines:
                draw.text((left, y), 'No bytes.', font=small, fill=_FAINT); y += 22
            top = y - 6
            for line in lines:
                if y > MAX_HEIGHT - 120:
                    omitted += 1; continue
                draw.text((left + 16, y), line, font=body, fill=_INK); y += 22
            if lines:
                draw.line((left, top, left, y), fill=_ACCENT if section['kind'] == 'text' else _RULE, width=3)
        if omitted:
            unit = 'more bytes' if section['kind'] == 'hex' else 'more rows' if section['kind'] == 'fields' else 'more lines'
            draw.text((left + 16, y + 2), '… %d %s not drawn; the complete data is in the saved record.' % (omitted, unit), font=small, fill=_MUTED); y += 24
        y += 20

    for line in [text for note in document.get('notes', [])[:4] for text in wrap(note, small, right - left)]:
        draw.text((left, y), line, font=small, fill=_MUTED); y += 20
    y += 12
    draw.line((left, y, right, y), fill=_RULE, width=1); y += 12
    for line in wrap(document.get('footer', ''), small, right - left)[:3]:
        draw.text((left, y), line, font=small, fill=_FAINT); y += 20
    height = min(MAX_HEIGHT, max(POSTER_HEIGHT, y + 28))
    canvas = canvas.crop((0, 0, WIDTH, height))
    ImageDraw.Draw(canvas).rectangle((0, 0, 7, height), fill=_ACCENT)
    info = PngInfo()
    info.add_text('Software', RENDERER)
    info.add_text('Description', describe(document))
    out = BytesIO()
    canvas.quantize(colors=96, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(out, format='PNG', pnginfo=info, optimize=True)
    return out.getvalue()


def poster(png):
    """Top of an evidence image at the gallery's landscape proportion."""
    from PIL import Image
    with Image.open(BytesIO(png), formats=('PNG',)) as image:
        image.load()
        return image.crop((0, 0, image.width, min(image.height, POSTER_HEIGHT))).convert('RGB')
