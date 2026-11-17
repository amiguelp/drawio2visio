#!/usr/bin/env python3
"""Turn a draw.io geometry dump into a flat, Visio-friendly draw.io diagram.

    python3 flatten_for_visio.py dump.json flat.drawio [--font "Segoe UI"]
                                 [--image-map map.json] [--webroot DIR]

Why each transformation exists (Visio import/export loses these otherwise):
  * Edges become unglued polylines with the exact points draw.io routed.
    Visio re-routes glued "dynamic connectors"; fixed points keep the shape.
  * Labels that sit outside their shape (icons with the name below, labels
    left/right/top) become separate text boxes at the position draw.io used.
    The exporter otherwise squeezes them into the icon box and they wrap.
  * Multi-style labels (title + grey subtitle) become one text box per line.
  * Swimlanes become a body rectangle + a header rectangle; nested containers
    are flattened to absolute coordinates (relative child coordinates and
    swimlane headers are what the exporter gets wrong).
  * Edge end points that land on a shape's edge right where its label sits are
    moved to the far side of the label, so arrows never cross text.
  * Icons are rasterized at high resolution (the exporter embeds SVGs at their
    tiny intrinsic size) and, when nothing overlaps them, flattened onto the
    colour behind them so no renderer can paint a box behind the alpha.
  * Fonts are replaced by one installed on Windows (default Segoe UI), since
    font substitution is the main reason texts move in Visio.

Writes <flat.drawio>.meta.json for post_vsdx.py and prints warnings (e.g.
images that could not be resolved locally).
"""
import argparse, base64, hashlib, html, io, json, os, re, sys, urllib.parse

ap = argparse.ArgumentParser()
ap.add_argument('dump'); ap.add_argument('out')
ap.add_argument('--font', default='Segoe UI')
ap.add_argument('--image-map', default=None, help='JSON {original url or file name: local file path}')
ap.add_argument('--webroot', default=os.path.join(os.environ.get('DRAWIO_VSDX_HOME', os.path.expanduser('~/.cache/drawio-vsdx')), 'webroot'))
args = ap.parse_args()

from PIL import Image
try:
    if os.environ.get('DRAWIO_NO_CAIRO'):
        raise ImportError
    import cairosvg
except Exception:          # en Windows cairosvg no tiene la DLL de cairo: se usa el navegador
    cairosvg = None

dump = json.load(open(args.dump, encoding='utf-8'))
WEB = args.webroot
HIRES = os.path.join(WEB, '_hires'); os.makedirs(HIRES, exist_ok=True)
SVGDIR = os.path.join(HIRES, 'svg'); os.makedirs(SVGDIR, exist_ok=True)
pending = []   # SVG que hay que rasterizar con el navegador (sin cairosvg)
FONT = 'fontFamily=%s;' % args.font
imap = json.load(open(args.image_map, encoding='utf-8')) if args.image_map else {}
warnings = []

# ---------------------------------------------------------------- helpers
def st(style):
    d = {}
    for part in (style or '').split(';'):
        if not part:
            continue
        if '=' in part:
            k, v = part.split('=', 1); d[k] = v
        else:
            d[part] = '1'
    return d

def style_str(d):
    return ''.join('%s=%s;' % (k, v) if v != '1' or k in NUMERIC_KEYS else '%s;' % k for k, v in d.items())
NUMERIC_KEYS = {'rounded', 'dashed', 'endFill', 'startFill', 'fontStyle', 'strokeWidth', 'html', 'aspect', 'opacity',
                'shadow', 'horizontal', 'arcSize', 'spacing', 'spacingLeft', 'spacingRight', 'spacingTop', 'spacingBottom',
                'fontSize', 'endSize', 'startSize', 'curved', 'rotation', 'imageAspect', 'fillOpacity', 'strokeOpacity'}

def r(v):
    return round(v + 1e-9, 2)

def q(v):
    return '"' + html.escape(str(v), quote=True) + '"'

def is_on(d, k):
    return d.get(k) == '1'

def bold_of(d):
    try:
        return int(d.get('fontStyle', '0')) & 1 == 1
    except ValueError:
        return False

def parse_label(value, d):
    """-> list of lines: {text, size, color, bold}. Handles draw.io HTML labels."""
    fc = d.get('fontColor', '#000000')
    base = {'size': float(d.get('fontSize', '12')), 'color': '#000000' if fc == 'default' else fc, 'bold': bold_of(d)}
    if not value:
        return []
    if d.get('html') != '1' and '<' not in value:
        return [dict(base, text=t) for t in value.split('\n') if t.strip()]
    v = re.sub(r'(?i)<br\s*/?>|</div>|</p>|</li>', '\n', value)
    lines = []
    for raw in v.split('\n'):
        size = re.search(r'font-size:\s*([\d.]+)px', raw)
        color = re.search(r'color[=:]\s*"?\s*(#[0-9A-Fa-f]{6}|#[0-9A-Fa-f]{3})', raw)
        bold = re.search(r'(?i)<b[\s>]|<strong|font-weight:\s*(bold|[6-9]00)', raw)
        notbold = re.search(r'font-weight:\s*(normal|[1-4]00)', raw)
        text = html.unescape(re.sub(r'<[^>]+>', '', raw)).replace('\xa0', ' ').strip()
        if text:
            lines.append({'text': text, 'size': float(size.group(1)) if size else base['size'],
                          'color': color.group(1) if color else base['color'],
                          'bold': (base['bold'] or bool(bold)) and not notbold})
    return lines

# fonts present in every Windows install: kept as they are (Visio renders them with the
# same metrics as draw.io); anything else becomes --font so Visio does not substitute it
WIN_FONTS = {f.lower() for f in ('Arial', 'Arial Black', 'Calibri', 'Calibri Light', 'Cambria', 'Candara', 'Consolas',
             'Constantia', 'Corbel', 'Courier New', 'Franklin Gothic Medium', 'Gabriola', 'Georgia', 'Impact',
             'Lucida Console', 'Lucida Sans Unicode', 'Palatino Linotype', 'Segoe UI', 'Segoe UI Light',
             'Segoe UI Semibold', 'Sitka Text', 'Sylfaen', 'Tahoma', 'Times New Roman', 'Trebuchet MS', 'Verdana')}

def visio_font(f):
    return f if f and f.lower() in WIN_FONTS else args.font

def label_lines(o, d):
    """Lines of the label with per-run format. Uses what draw.io actually painted
    (dump 'runs': computed colour, weight, size...) and falls back to parsing the value."""
    runs = o.get('runs')
    if runs:
        out = []
        for segs in runs:
            segs = [dict(r, text=r['text'].replace('\xa0', ' '), font=visio_font(r.get('font'))) for r in segs]
            text = ''.join(r['text'] for r in segs).strip()
            if not text:
                continue
            out.append({'text': text, 'size': max(r['size'] for r in segs), 'color': segs[0]['color'],
                        'bold': all(r['bold'] for r in segs if r['text'].strip()), 'segs': segs})
        return out
    lines = parse_label(o.get('value', ''), d)
    for l in lines:
        l['segs'] = [{'text': l['text'], 'size': l['size'], 'color': l['color'], 'bold': bool(l['bold']),
                      'italic': False, 'underline': False, 'font': visio_font(d.get('fontFamily'))}]
    return lines

def bg_of(d, text_cell=False):
    """Background colour draw.io paints behind a label (None = transparent)."""
    lbg = d.get('labelBackgroundColor')
    if lbg == 'default':
        return '#FFFFFF'
    if lbg and lbg != 'none':
        return lbg
    if text_cell:
        f = d.get('fillColor')
        if f and f not in ('none', 'default'):
            return f
    return None

def runs_of(lines):
    return [{'segs': l['segs']} for l in lines]

def text_width(text, size, bold):
    w = sum(0.72 if c.isupper() else 0.33 if c in ' .,:;·|il!' else 0.57 for c in text) * size
    return w * (1.06 if bold else 1.0)

def inside(inner, outer, tol=2):
    x, y, w, h = inner; X, Y, W, H = outer
    return x >= X - tol and y >= Y - tol and x + w <= X + W + tol and y + h <= Y + H + tol

def overlap(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]

# ---------------------------------------------------------------- images
_index = None
def find_local(name):
    global _index
    if _index is None:
        _index = {}
        for root, _, files in os.walk(os.path.join(WEB, 'img')):
            for f in files:
                _index.setdefault(f.lower(), os.path.join(root, f))
    return _index.get(name.lower())

def image_bytes(url):
    """Return (bytes, kind) for an image reference, or (None, None)."""
    if url in imap or os.path.basename(url) in imap:
        p = imap.get(url) or imap.get(os.path.basename(url))
        return open(p, 'rb').read(), os.path.splitext(p)[1].lower().lstrip('.')
    if url.startswith('data:image/'):
        head, _, data = url.partition(',')
        kind = 'svg' if 'svg' in head else 'raster'
        try:
            return base64.b64decode(data + '=' * (-len(data) % 4)), kind
        except Exception:
            return urllib.parse.unquote(data).encode('utf-8'), kind
    m = re.search(r'/img/lib/(.+)$', url)
    cands = []
    if m:
        cands.append(os.path.join(WEB, 'img', 'lib', m.group(1)))
    if url.startswith('__WEBROOT__/'):
        cands.append(os.path.join(WEB, url[len('__WEBROOT__/'):]))
    for c in cands:
        if os.path.exists(c):
            return open(c, 'rb').read(), os.path.splitext(c)[1].lower().lstrip('.')
    loc = find_local(os.path.basename(urllib.parse.urlparse(url).path))
    if loc:
        return open(loc, 'rb').read(), os.path.splitext(loc)[1].lower().lstrip('.')
    return None, None

def hires_image(url, box, bg):
    """Rasterize to >=4x the display size; bg=None keeps transparency."""
    data, kind = image_bytes(url)
    if data is None:
        warnings.append('imagen no encontrada localmente: %s' % url[:120])
        return url
    target = int(min(4000, max(256, 4 * max(box[2], box[3]))))
    try:
        if kind == 'svg' or data.lstrip()[:5] in (b'<?xml', b'<svg ') or b'<svg' in data[:300]:
            key = '%s_%d' % (hashlib.sha1(data).hexdigest()[:16], target)
            cached = os.path.join(SVGDIR, key + '.png')
            if os.path.exists(cached):
                im = Image.open(cached).convert('RGBA')
            elif cairosvg is not None:
                png = cairosvg.svg2png(bytestring=data, output_width=target)
                im = Image.open(io.BytesIO(png)).convert('RGBA')
            else:
                svgp = os.path.join(SVGDIR, key + '.svg')
                with open(svgp, 'wb') as f:
                    f.write(data)
                pending.append({'svg': svgp, 'png': cached, 'width': target})
                return url
        else:
            im = Image.open(io.BytesIO(data)).convert('RGBA')
            if max(im.size) < target:
                f = target / max(im.size)
                im = im.resize((max(1, round(im.width * f)), max(1, round(im.height * f))), Image.LANCZOS)
                if max(Image.open(io.BytesIO(data)).size) < 2 * max(box[2], box[3]):
                    warnings.append('imagen de baja resolución (se ampliará con pérdida): %s' % url[:80])
    except Exception as e:
        warnings.append('no se pudo rasterizar %s (%s)' % (url[:80], e))
        return url
    if bg:
        canvas = Image.new('RGBA', im.size, bg); canvas.alpha_composite(im); im = canvas.convert('RGB')
    h = hashlib.sha1(data + str(bg).encode()).hexdigest()[:16]
    name = '%s.png' % h
    im.save(os.path.join(HIRES, name), optimize=True)
    return '__WEBROOT__/_hires/' + name

# ---------------------------------------------------------------- emit
cells, meta = [], []
uid = [0]
id2idx = {}   # draw.io cell id -> meta index of the Visio shape that represents it
keys = []     # z-order key of every emitted cell (draw.io model order)
cur_key = [0]
cur_alpha = [None]   # transparencies of the cell being emitted (opacity, fillOpacity, ...)

def alpha_of(d):
    op = float(d.get('opacity', '100') or 100) / 100
    a = {'fill': 1 - op * float(d.get('fillOpacity', '100') or 100) / 100,
         'line': 1 - op * float(d.get('strokeOpacity', '100') or 100) / 100,
         'text': 1 - float(d.get('textOpacity', '100') or 100) / 100,
         'img': 1 - op}
    return {k: round(v, 4) for k, v in a.items() if v > 0.001} or None
gid = [0]

def group_from(start):
    """Marca como un grupo de Visio los vértices emitidos desde meta[start]."""
    idx = [i for i in range(start, len(meta)) if meta[i]['kind'] == 'v']
    if len(idx) > 1:
        gid[0] += 1
        for i in idx:
            meta[i]['group'] = gid[0]

def corner_radius(d, w, h, edge=False):
    """Corner radius in px exactly as draw.io draws it (mxRectangleShape / mxConnector)."""
    if not is_on(d, 'rounded'):
        return 0
    if edge:
        return float(d.get('arcSize', '20')) / 2
    if d.get('absoluteArcSize') == '1':
        return min(w / 2, h / 2, float(d.get('arcSize', '20')) / 2)
    f = float(d.get('arcSize', '15')) / 100
    return min(w * f, h * f)

def emit_vertex(style, value, b, m):
    uid[0] += 1
    cells.append('<mxCell id="v%d" value=%s style=%s vertex="1" parent="1"><mxGeometry x="%s" y="%s" width="%s" height="%s" as="geometry"/></mxCell>'
                 % (uid[0], q(value), q(style), r(b[0]), r(b[1]), r(b[2]), r(b[3])))
    meta.append(dict({'kind': 'v', 'text': bool(value), 'alpha': cur_alpha[0]}, **m))
    keys.append(cur_key[0])

def emit_edge(style, pts):
    uid[0] += 1
    s, t, mid = pts[0], pts[-1], pts[1:-1]
    arr = ('<Array as="points">' + ''.join('<mxPoint x="%s" y="%s"/>' % (r(a), r(b)) for a, b in mid) + '</Array>') if mid else ''
    cells.append('<mxCell id="e%d" style=%s edge="1" parent="1"><mxGeometry relative="1" as="geometry"><mxPoint x="%s" y="%s" as="sourcePoint"/><mxPoint x="%s" y="%s" as="targetPoint"/>%s</mxGeometry></mxCell>'
                 % (uid[0], q(style), r(s[0]), r(s[1]), r(t[0]), r(t[1]), arr))
    meta.append({'kind': 'e', 'alpha': cur_alpha[0]})
    keys.append(cur_key[0])

def text_style(align, valign, line, extra=''):
    return ('text;html=0;whiteSpace=wrap;strokeColor=none;fillColor=none;spacing=0;align=%s;verticalAlign=%s;fontSize=%s;fontColor=%s;fontStyle=%d;%s%s'
            % (align, valign, ('%g' % line['size']), line['color'], 1 if line['bold'] else 0, FONT, extra))

def emit_layout(lay):
    """Rebuild an HTML table label as laid out by the browser: cell/row fills,
    border lines and one text box per cell (text wraps inside the cell width)."""
    plain = {'align': 'center', 'valign': 'middle', 'margins': (0, 0, 0, 0), 'bold': False, 'image': False}
    for f in lay.get('fills', []):
        emit_vertex('rounded=0;html=0;strokeColor=none;fillColor=%s;' % f['color'], '', f['b'], dict(plain))
    for ru in lay.get('rules', []):
        emit_edge('html=1;endArrow=none;startArrow=none;strokeColor=%s;strokeWidth=%s;%s'
                  % (ru['color'], r(max(0.25, ru['w'])), 'dashed=1;' if ru.get('dashed') else ''), ru['p'])
    for blk in lay.get('blocks', []):
        lines = label_lines({'runs': blk['runs']}, {})
        if not lines:
            continue
        x, y, w, h = blk['b']
        al = blk.get('align', 'left')
        # exact cell width (+1 % for rounding) so Visio wraps where the browser did;
        # a little more only when the font had to be replaced
        same = all(r.get('font', '').lower() in WIN_FONTS for ln in blk['runs'] for r in ln)
        # lines already broken where draw.io broke them: room so that Visio does not re-wrap
        k = 0.08 if blk.get('exact') else (0.01 if same else 0.05)
        box = (x - (w * k / 2 if al == 'center' else (w * k if al == 'right' else 0)), y, w * (1 + k), h)
        m = {'align': al, 'valign': 'top', 'margins': (0, 0, 0, 0), 'bold': False, 'image': False,
             'runs': runs_of(lines)}
        emit_vertex(text_style(al, 'top', lines[0]), '\n'.join(l['text'] for l in lines), box, m)

def emit_text_block(lines, box, align='center', valign='top', extra='', bg=None):
    """A single text box for the whole label. When the lines differ in size,
    colour or weight, post_vsdx writes one paragraph per line with its own
    character format (meta 'runs'). Width sized for Visio fonts."""
    if not lines:
        return []
    x, y, w, h = box
    total = sum(l['size'] * 1.3 for l in lines)
    if valign == 'middle':
        y = y + (h - total) / 2
    elif valign == 'bottom':
        y = y + h - total
    tw = max(w, max(text_width(l['text'], l['size'], l['bold']) for l in lines) + 14)
    tx = x + w / 2 - tw / 2 if align == 'center' else (x if align == 'left' else x + w - tw)
    fill = 'fillColor=%s;' % bg if bg else ''
    style = text_style(align, 'top', lines[0], extra + fill).replace('fillColor=none;' if bg else '\u00a7', '')
    m = {'align': align, 'valign': 'top', 'margins': (0, 0, 0, 0), 'bold': lines[0]['bold'], 'image': False, 'textbg': bg}
    m['runs'] = runs_of(lines)
    m['bold'] = False
    emit_vertex(style, '\n'.join(l['text'] for l in lines), (tx, y, tw, total), m)
    return [(tx, y, tw, total)]

# containers: body colours for "what is behind an image"
bodies = []   # (box, colour)
label_boxes = []   # (owner_box, label_box)
image_boxes = [o['b'] for o in dump if not o['edge'] and (o.get('raster') or 'image' in st(o['style']) or st(o['style']).get('shape') == 'image')]

edges = []
for k, o in enumerate(dump):
    o['_k'] = k
    cur_key[0] = k
    cur_alpha[0] = alpha_of(st(o['style']))
    d = st(o['style'])
    if o['edge']:
        edges.append(o); continue
    b = o['b']
    if 'group' in d and not o['value']:
        continue
    lines = label_lines(o, d)
    layout = o.get('layout')
    if layout:
        lines = []          # the label is rebuilt piece by piece after the shape

    # ---- swimlanes / titled containers
    if 'swimlane' in d:
        g0 = len(meta)
        id2idx[o['id']] = g0
        ss = float(d.get('startSize', '23'))
        horiz = d.get('horizontal', '1') != '0'
        dash = 'dashed=1;dashPattern=%s;' % d.get('dashPattern', '8 4') if is_on(d, 'dashed') else ''
        stroke = 'strokeColor=%s;strokeWidth=%s;' % (d.get('strokeColor', '#000000'), d.get('strokeWidth', '1'))
        bodyfill = d.get('swimlaneFillColor', 'none')
        emit_vertex('rounded=0;html=1;fillColor=%s;%s%s' % (bodyfill, stroke, dash), '', b,
                    {'align': 'center', 'valign': 'middle', 'margins': (0, 0, 0, 0), 'bold': False, 'image': False})
        if bodyfill not in ('none', ''):
            bodies.append((b, bodyfill))
        hb = (b[0], b[1], b[2], ss) if horiz else (b[0], b[1], ss, b[3])
        emit_vertex('rounded=0;html=1;fillColor=%s;%s%s' % (d.get('fillColor', '#FFFFFF'), stroke, dash), '', hb,
                    {'align': 'center', 'valign': 'middle', 'margins': (0, 0, 0, 0), 'bold': False, 'image': False})
        if lines:
            al = d.get('align', 'center')
            sp = float(d.get('spacingLeft', '0')) + float(d.get('spacing', '2'))
            if horiz:
                tb = (hb[0] + (sp if al == 'left' else 0), hb[1], hb[2] - (sp if al == 'left' else 0), hb[3])
                emit_text_block(lines, tb, al, 'middle')
                meta[-1]['owner'] = g0 + 1        # title = text of the header
            else:
                vm = {'align': 'center', 'valign': 'middle', 'margins': (0, 0, 0, 0), 'bold': lines[0]['bold'], 'image': False}
                vm['runs'] = runs_of(lines)
                vm['bold'] = False
                emit_vertex(text_style('center', 'middle', lines[0], 'rotation=-90;'), '\n'.join(l['text'] for l in lines),
                            (hb[0] + ss / 2 - hb[3] / 2, hb[1] + hb[3] / 2 - ss / 2, hb[3], ss), vm)
        group_from(g0)
        continue

    # ---- plain text cells
    if 'text' in d and not d.get('shape') and not o.get('raster'):
        id2idx[o['id']] = len(meta)
        emit_text_block(lines, b, d.get('align', 'center'), d.get('verticalAlign', 'middle'), bg=bg_of(d, True))
        continue

    if o.get('raster'):                 # stencil or pasted picture drawn by draw.io
        d = {k: v for k, v in d.items() if k not in ('shape', 'fillColor', 'strokeColor', 'gradientColor')}
        d['image'] = o['raster']
        if o.get('rkind') == 'img':
            lines = []
    # Forma «label» de draw.io (tarjeta con icono y texto): draw.io pinta el icono dentro
    # de la tarjeta según imageAlign/imageVerticalAlign/imageWidth/imageHeight. Se separa
    # en tarjeta (con su texto) + icono aparte, agrupados; si no, el icono ocupaba toda
    # la tarjeta y tapaba el texto.
    label_img = None
    if 'image' in d and ('label' in d or d.get('shape') == 'label') and not o.get('raster'):
        iw = float(d.get('imageWidth', '24')); ih = float(d.get('imageHeight', '24'))
        spc = float(d.get('spacing', '2')) + 5          # mxLabel: spacing + 5
        ia = d.get('imageAlign', 'left'); iva = d.get('imageVerticalAlign', 'middle')
        ix = b[0] + (b[2] - iw) / 2 if ia == 'center' else (b[0] + b[2] - iw - spc if ia == 'right' else b[0] + spc)
        iy = b[1] + (b[3] - ih) / 2 if iva == 'middle' else (b[1] + b[3] - ih - spc if iva == 'bottom' else b[1] + spc)
        label_img = (d['image'], (ix, iy, iw, ih))
        d = {k: v for k, v in d.items() if k not in ('image', 'imageWidth', 'imageHeight', 'imageAlign',
                                                     'imageVerticalAlign', 'imageAspect', 'label', 'shape')}
    is_image = 'image' in d or d.get('shape') == 'image'
    tb = o.get('tb')
    outside = bool(tb) and not inside(tb, b)
    multi = len({(r['size'], r['color'], r['bold'], r.get('italic'), r.get('underline')) for l in lines for r in l['segs']}) > 1

    g0 = len(meta)
    id2idx[o['id']] = g0
    if is_image:
        # keep the transparency, as in draw.io (Visio honours PNG alpha); filling it with
        # a guessed background colour left white boxes on coloured containers
        img = hires_image(d.get('image', ''), b, None)
        emit_vertex('shape=image;html=1;imageAspect=0;aspect=fixed;image=%s;' % img, '', b,
                    {'align': 'center', 'valign': 'middle', 'margins': (0, 0, 0, 0), 'bold': False, 'image': True})
    else:
        keep = {k: v for k, v in d.items() if k not in ('container', 'collapsible', 'fontSource', 'fontFamily', 'html',
                                                        'labelBackgroundColor', 'labelBorderColor', 'labelPosition',
                                                        'verticalLabelPosition', 'whiteSpace', 'expand', 'recursiveResize',
                                                        'pointerEvents', 'connectable',
                                                        'shadow')}   # la sombra del exportador sale como un borde negro grueso;
                                                                    # post_vsdx pone una sombra suave de Visio
        # draw.io's implicit defaults ("default" or missing) must be explicit, or the
        # exporter writes black fills
        for k, dv in (('fillColor', '#FFFFFF'), ('strokeColor', '#000000'), ('fontColor', '#000000')):
            if keep.get(k, 'default') == 'default':
                keep[k] = dv
        in_text = '' if outside else '\n'.join(l['text'] for l in lines)
        if in_text:
            keep['fontSize'] = '%g' % lines[0]['size']; keep['fontColor'] = lines[0]['color']
            keep['fontStyle'] = str((int(keep.get('fontStyle', '0') or 0) & ~1) | (1 if lines[0]['bold'] else 0))
        sp = float(d.get('spacing', '2'))
        m = {'rounding': corner_radius(d, b[2], b[3]),
             'align': d.get('align', 'center'), 'valign': d.get('verticalAlign', 'middle'), 'bold': bool(in_text) and lines[0]['bold'],
             'image': False, 'margins': (sp + float(d.get('spacingLeft', '0')), sp + float(d.get('spacingRight', '0')),
                                         sp + float(d.get('spacingTop', '0')), sp + float(d.get('spacingBottom', '0')))}
        if in_text:
            m['runs'] = runs_of(lines)
            m['bold'] = False
        if is_on(d, 'shadow'):
            m['shadow'] = True          # post_vsdx escribe una sombra suave de Visio
        if label_img:
            # el texto centrado de las tarjetas suele ir justo de ancho: sin márgenes
            # laterales Visio no lo parte en dos líneas
            m['margins'] = (0, 0) + m['margins'][2:]
        emit_vertex('html=0;whiteSpace=wrap;' + style_str(keep) + FONT, in_text, b, m)
        if label_img:
            ib = label_img[1]
            emit_vertex('shape=image;html=1;imageAspect=0;aspect=fixed;image=%s;' % hires_image(label_img[0], ib, None), '', ib,
                        {'align': 'center', 'valign': 'middle', 'margins': (0, 0, 0, 0), 'bold': False, 'image': True})
            image_boxes.append(ib)
        if in_text:
            if label_img:
                group_from(g0)
            continue
    if layout:
        emit_layout(layout)
        group_from(g0)
        continue
    if lines:
        box = tb if tb else (b[0], b[1] + b[3] + 2, b[2], sum(l['size'] * 1.3 for l in lines))
        placed = emit_text_block(lines, box, d.get('align', 'center') if not is_image else 'center',
                                 'top' if (is_image or outside) else d.get('verticalAlign', 'middle'), bg=bg_of(d))
        if placed:
            meta[-1]['owner'] = g0                # the label is the text of its shape
        if placed:
            x0 = min(p[0] for p in placed); y0 = min(p[1] for p in placed)
            x1 = max(p[0] + p[2] for p in placed); y1 = max(p[1] + p[3] for p in placed)
            # use the real text extent (not the padded box) for arrow clearance
            tw = max(text_width(l['text'], l['size'], l['bold']) for l in lines)
            cx = (x0 + x1) / 2
            label_boxes.append((b, (cx - tw / 2, y0, tw, y1 - y0)))
        group_from(g0)

# ---------------------------------------------------------------- edges
def snap(pts):
    pts = [list(p) for p in pts]
    for i in range(1, len(pts)):
        a, c = pts[i - 1], pts[i]
        if abs(a[0] - c[0]) <= 1.5 and abs(a[1] - c[1]) > 1.5:
            c[0] = a[0]
        elif abs(a[1] - c[1]) <= 1.5 and abs(a[0] - c[0]) > 1.5:
            c[1] = a[1]
    return pts

def clear_label(pts, end):
    """If the arrow end touches a shape on the side where that shape's label is,
    move the end beyond the label so the line never runs over the text."""
    i, j = (-1, -2) if end else (0, 1)
    if len(pts) < 2:
        return pts
    px, py = pts[i]; qx, qy = pts[j]
    for ob, lb in label_boxes:
        x, y, w, h = ob; lx, ly, lw, lh = lb
        if abs(px - qx) < 0.5:                              # vertical segment
            if qy > py and abs(py - (y + h)) <= 2 and ly >= y + h - 4 and lx - 2 <= px <= lx + lw + 2:
                pts[i] = [px, ly + lh + 3]                  # comes from below, label below
            elif qy < py and abs(py - y) <= 2 and ly + lh <= y + 4 and lx - 2 <= px <= lx + lw + 2:
                pts[i] = [px, ly - 3]                       # comes from above, label above
        elif abs(py - qy) < 0.5:                            # horizontal segment
            if qx > px and abs(px - (x + w)) <= 2 and lx >= x + w - 4 and ly - 2 <= py <= ly + lh + 2:
                pts[i] = [lx + lw + 3, py]                  # from the right, label on the right
            elif qx < px and abs(px - x) <= 2 and lx + lw <= x + 4 and ly - 2 <= py <= ly + lh + 2:
                pts[i] = [lx - 3, py]                       # from the left, label on the left
    return pts

EDGE_KEYS = ('strokeColor', 'strokeWidth', 'dashed', 'dashPattern', 'endArrow', 'endFill', 'endSize',
             'startArrow', 'startFill', 'startSize', 'opacity', 'curved')
for o in edges:
    cur_key[0] = o['_k']
    cur_alpha[0] = alpha_of(st(o['style']))
    d = st(o['style'])
    pts = clear_label(clear_label(snap(o['pts']), True), False)
    keep = {k: d[k] for k in EDGE_KEYS if k in d}
    keep.setdefault('endArrow', 'classic')
    if keep.get('strokeColor', 'default') == 'default':
        keep['strokeColor'] = '#000000'
    emit_edge('html=1;rounded=%s;' % d.get('rounded', '0') + style_str(keep), pts)
    meta[-1]['rounding'] = corner_radius(d, 0, 0, edge=True)
    # glue: the shape at each end (resolved to Visio shape indices)
    for end, key in (('src', 'source'), ('tgt', 'target')):
        if o.get(key):
            meta[-1][end + '_id'] = o[key]
    lines = label_lines(o, d)
    if lines and o.get('tb'):
        lbg = d.get('labelBackgroundColor')
        bg = '#FFFFFF' if lbg in (None, 'default') and lines else (None if lbg == 'none' else lbg)
        emit_text_block(lines, o['tb'], 'center', 'middle', bg=bg)

for m in meta:
    for end in ('src', 'tgt'):
        if m.get(end + '_id') in id2idx:
            m[end] = id2idx[m[end + '_id']]

# same stacking order as draw.io: everything in model order (edges were computed last
# because they need every label box, but they go back to their place)
perm = sorted(range(len(cells)), key=lambda i: keys[i])
newpos = {old: new for new, old in enumerate(perm)}
cells = [cells[i] for i in perm]
meta = [meta[i] for i in perm]
for m in meta:
    for f in ('owner', 'src', 'tgt'):
        if m.get(f) is not None:
            m[f] = newpos[m[f]]

# ---------------------------------------------------------------- page
minx = min([c for o in dump if not o['edge'] for c in [o['b'][0]]] + [p[0] for o in edges for p in o['pts']])
miny = min([c for o in dump if not o['edge'] for c in [o['b'][1]]] + [p[1] for o in edges for p in o['pts']])
shift_x, shift_y = 20 - minx, 20 - miny
def shift(m):
    return '%s="%s"' % (m.group(1), r(float(m.group(2)) + (shift_x if m.group(1) == 'x' else shift_y)))
body = re.sub(r'\b(x|y)="(-?[\d.]+)"', shift, ''.join(cells))
xml = ('<mxfile host="drawio"><diagram name="Visio" id="visio1"><mxGraphModel grid="0" page="1" pageScale="1" math="0" shadow="0">'
       '<root><mxCell id="0"/><mxCell id="1" parent="0"/>' + body + '</root></mxGraphModel></diagram></mxfile>')
open(args.out, 'w', encoding='utf-8').write(xml)
json.dump(meta, open(args.out + '.meta.json', 'w'))
pend = args.out + '.pending.json'
if pending:
    json.dump(pending, open(pend, 'w'))
    print('svg pendientes:', len(pending))
elif os.path.exists(pend):
    os.remove(pend)
print('cells:', len(cells))
for w in dict.fromkeys(warnings):
    print('AVISO:', w)
