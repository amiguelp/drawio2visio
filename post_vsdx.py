#!/usr/bin/env python3
"""Make the draw.io VSDX export deterministic in Visio.

    python3 post_vsdx.py in.vsdx flat.drawio.meta.json out.vsdx [--page A4|A3|Letter|fit] [--orientation auto|portrait|landscape]

  * text blocks: exactly the shape box, explicit horizontal/vertical alignment,
    explicit margins and bold (the exporter leaves offset text blocks that make
    Visio place text differently from draw.io)
  * images: no fill / no line behind the bitmap
  * connectors: ConFixedCode=2 (Visio never re-routes them), no text background
  * resizable shapes: geometry, image and text-block cells get formulas
    relative to Width/Height (the exporter writes fixed numbers, so in Visio
    the handles moved but the drawn shape did not change size)
  * groups: icon + its name, shape + multi-line text, container + header are
    Visio groups, so they move together
  * page: chosen paper size, content centred; if it does not fit, the page is
    enlarged to the content (reported) instead of clipping
"""
import argparse, html, json, re, zipfile

ap = argparse.ArgumentParser()
ap.add_argument('src'); ap.add_argument('meta'); ap.add_argument('dst')
ap.add_argument('--page', default='A4')
ap.add_argument('--orientation', default='auto')
ap.add_argument('--no-group', action='store_true')
ap.add_argument('--no-glue', action='store_true')
ap.add_argument('--separate-labels', action='store_true')
a = ap.parse_args()

meta = json.load(open(a.meta))
PAPERS = {'A4': (8.267716535, 11.692913386), 'A3': (11.692913386, 16.535433071), 'LETTER': (8.5, 11.0)}

zin = zipfile.ZipFile(a.src)
page = zin.read('visio/pages/page1.xml').decode('utf-8')
pages = zin.read('visio/pages/pages.xml').decode('utf-8')
shapes = re.findall(r'<Shape ID="\d+".*?</Shape>(?=<Shape ID=|</Shapes>)', page, re.S)
if len(shapes) != len(meta):
    raise SystemExit('shape count %d != meta %d: the exporter skipped or added shapes' % (len(shapes), len(meta)))

def cell(sh, name):
    m = re.search(r'<Cell N="%s" V="([^"]*)"' % name, sh)
    return float(m.group(1)) if m else None

def set_cell(sh, name, value):
    pat = r'<Cell N="%s" V="[^"]*"(?: F="[^"]*")?(?: U="[^"]*")?/>' % name
    new = '<Cell N="%s" V="%s"/>' % (name, value)
    if re.search(pat, sh):
        return re.sub(pat, new, sh, count=1)
    return re.sub(r'(<Cell N="LocPinY" V="[^"]*"(?: F="[^"]*")?/>)', lambda m: m.group(1) + new, sh, count=1)

def drop_cell(sh, name):
    return re.sub(r'<Cell N="%s" V="[^"]*"(?: F="[^"]*")?/>' % name, '', sh)

def fcell(sh, name, base, total, count=1):
    """Give cell `name` the formula base*(value/total) keeping its value."""
    def rep(m):
        v = float(m.group(1))
        ratio = v / total if total else 0
        return '<Cell N="%s" V="%r" F="%s*%r"/>' % (name, v, base, round(ratio, 10))
    return re.sub(r'<Cell N="%s" V="(-?[\d.eE+-]+)"(?: F="[^"]*")?(?: U="[^"]*")?/>' % name, rep, sh, count=count)

GEO_CELLS = {'MoveTo': 'XY', 'LineTo': 'XY', 'ArcTo': 'XY', 'RelMoveTo': '', 'PolylineTo': 'XY',
             'EllipticalArcTo': 'XYAB', 'Ellipse': 'XYABCD', 'SplineStart': 'XY', 'SplineKnot': 'XY',
             'NURBSTo': 'XY', 'InfiniteLine': 'XYAB'}

def relativize(sh, w, h):
    if not w or not h:
        return sh
    for name, base, tot in (('LocPinX', 'Width', w), ('LocPinY', 'Height', h), ('ImgWidth', 'Width', w),
                            ('ImgHeight', 'Height', h), ('ImgOffsetX', 'Width', w), ('ImgOffsetY', 'Height', h),
                            ('TxtPinX', 'Width', w), ('TxtWidth', 'Width', w), ('TxtLocPinX', 'Width', w),
                            ('TxtPinY', 'Height', h), ('TxtHeight', 'Height', h), ('TxtLocPinY', 'Height', h)):
        sh = fcell(sh, name, base, tot)
    def row(m):
        body = m.group(0)
        for c in GEO_CELLS.get(m.group(1), ''):
            xy = c in 'XAC'
            body = fcell(body, c, 'Width' if xy else 'Height', w if xy else h)
        return body
    return re.sub(r'<Row T="(\w+)"[^>]*>.*?</Row>', row, sh, flags=re.S)

def rich_text(sh, runs, ha, text_trans=0):
    """Text exactly as draw.io paints it: one character run per piece of text with
    its own colour, size, bold, italic and underline; a paragraph per line."""
    tpl = re.search(r'<Section N="Character">\s*(<Row IX="0">.*?</Row>)', sh, re.S)
    row0 = tpl.group(1) if tpl else ('<Row IX="0"><Cell N="Color" V="#000000"/><Cell N="Size" V="0.1"/>'
                                     '<Cell N="Font" V="Segoe UI"/><Cell N="Style" V="0"/></Row>')
    rows, lines = [], []
    n = 0
    for ln in runs:
        parts = []
        for rn in ln['segs']:
            r = row0.replace('<Row IX="0">', '<Row IX="%d">' % n, 1)
            r = re.sub(r'<Cell N="Color" V="[^"]*"/>', '<Cell N="Color" V="%s"/>' % rn['color'], r)
            r = re.sub(r'<Cell N="Size" V="[^"]*"/>', '<Cell N="Size" V="%r"/>' % (rn['size'] / PX), r)
            style = (1 if rn.get('bold') else 0) | (2 if rn.get('italic') else 0) | (4 if rn.get('underline') else 0)
            r = re.sub(r'<Cell N="Style" V="[^"]*"/>', '<Cell N="Style" V="%d"/>' % style, r)
            if rn.get('font'):
                f = html.escape(rn['font'], True)
                r = re.sub(r'<Cell N="Font" V="[^"]*"(?: F="[^"]*")?/>', lambda _m: '<Cell N="Font" V="%s"/>' % f, r) \
                    if '<Cell N="Font"' in r else r.replace('</Row>', '<Cell N="Font" V="%s"/></Row>' % f)
            r = re.sub(r'<Cell N="ColorTrans" V="[^"]*"/>', '', r)
            if text_trans:
                r = r.replace('</Row>', '<Cell N="ColorTrans" V="%r"/></Row>' % text_trans)
            rows.append(r)
            parts.append('<cp IX="%d"/>%s' % (n, html.escape(rn['text'], False)))
            n += 1
        lines.append(''.join(parts))
    char = '<Section N="Character">%s</Section>' % ''.join(rows)
    if tpl:
        sh = re.sub(r'<Section N="Character">.*?</Section>', lambda m: char, sh, count=1, flags=re.S)
    else:
        sh = sh.replace('<Text>', char + '<Text>', 1)
    para = '<Section N="Paragraph"><Row IX="0"><Cell N="HorzAlign" V="%d"/></Row></Section>' % ha
    if '<Section N="Paragraph">' in sh:
        sh = re.sub(r'<Section N="Paragraph">.*?</Section>', para, sh, count=1, flags=re.S)
    else:
        sh = sh.replace('<Text>', para + '<Text>', 1)
    body = '\n'.join(lines)
    return re.sub(r'<Text>.*?</Text>', lambda m: '<Text><pp IX="0"/>%s</Text>' % body, sh, count=1, flags=re.S)

# draw.io px per inch as written by VsdxExport (1 px = 0.254 mm)
PX = 600 / 5.905511811023622

xs, ys = [], []
for sh, m in zip(shapes, meta):
    if m['kind'] == 'e':
        xs += [cell(sh, 'BeginX'), cell(sh, 'EndX')]; ys += [cell(sh, 'BeginY'), cell(sh, 'EndY')]
    else:
        px, py, w, h, lx, ly = (cell(sh, k) for k in ('PinX', 'PinY', 'Width', 'Height', 'LocPinX', 'LocPinY'))
        xs += [px - lx, px - lx + w]; ys += [py - ly, py - ly + h]
if not xs:   # pestaña vacía: página en blanco del tamaño elegido
    xs, ys = [0.0], [0.0]
    print('AVISO: la pestaña no tiene formas exportables; página vacía')
minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
cw, ch = maxx - minx, maxy - miny

if a.page.upper() == 'FIT':
    PW, PH = cw + 0.8, ch + 0.8
else:
    pw, ph = PAPERS[a.page.upper()]
    land = a.orientation == 'landscape' or (a.orientation == 'auto' and cw > ch)
    PW, PH = (ph, pw) if land else (pw, ph)
    if cw > PW - 0.2 or ch > PH - 0.2:
        print('AVISO: el contenido (%.2f x %.2f in) no cabe en %s; página ampliada al contenido' % (cw, ch, a.page))
        PW, PH = max(PW, cw + 0.8), max(PH, ch + 0.8)
dx = (PW - cw) / 2 - minx
dy = (PH - ch) / 2 - miny

out = []
for sh, m in zip(shapes, meta):
    for name, d in (('PinX', dx), ('BeginX', dx), ('EndX', dx), ('PinY', dy), ('BeginY', dy), ('EndY', dy)):
        v = cell(sh, name)
        if v is not None:
            sh = re.sub(r'<Cell N="%s" V="[^"]*"(?: F="[^"]*")?/>' % name, '<Cell N="%s" V="%r"/>' % (name, v + d), sh, count=1)
    # the exporter writes Rounding twice (0.1*width, then the canvas radius) and Visio
    # takes the first: replace both with draw.io's real corner radius
    sh = re.sub(r'<Cell N="Rounding" V="[^"]*"(?: F="[^"]*")?(?: U="[^"]*")?/>', '', sh)
    if m.get('rounding'):
        sh = set_cell(sh, 'Rounding', repr(m['rounding'] / PX))
    if m.get('shadow'):
        # sombra como la de draw.io: desplazada 2 px a la derecha y 3 px abajo, del
        # mismo tamaño que la forma, negra al 75 % de transparencia. La del
        # exportador es un bloque negro opaco.
        for k, v in (('ShdwPattern', 1), ('ShdwForegnd', '#000000'), ('ShdwForegndTrans', 0.75),
                     ('ShapeShdwType', 1), ('ShapeShdwOffsetX', repr(2 / PX)), ('ShapeShdwOffsetY', repr(-3 / PX)),
                     ('ShapeShdwScaleFactor', 1), ('ShapeShdwObliqueAngle', 0)):
            sh = set_cell(sh, k, v)
        # difuminado ligero como el de draw.io (~1,5 pt). V va siempre en pulgadas;
        # U solo indica la unidad que muestra la hoja de formas
        sh = set_cell(sh, 'ShapeShdwBlur', repr(1.5 / 72))
        sh = sh.replace('<Cell N="ShapeShdwBlur" V="%r"/>' % (1.5 / 72), '<Cell N="ShapeShdwBlur" V="%r" U="PT"/>' % (1.5 / 72), 1)
    if m['kind'] == 'e':
        sh = set_cell(sh, 'ConFixedCode', 2)
        sh = drop_cell(sh, 'TextBkgnd')
    else:
        w, h = cell(sh, 'Width'), cell(sh, 'Height')
        if m.get('image'):
            sh = set_cell(sh, 'LockAspect', 1)
            for k, v in (('FillPattern', 0), ('LinePattern', 0), ('FillForegnd', '#FFFFFF'), ('FillForegndTrans', 1),
                         ('FillBkgndTrans', 1), ('LineColor', '#FFFFFF'), ('LineColorTrans', 1)):
                sh = set_cell(sh, k, v)
        if m.get('text') and '<Text>' in sh:
            for k, v in (('TxtPinX', w / 2), ('TxtPinY', h / 2), ('TxtWidth', w), ('TxtHeight', h),
                         ('TxtLocPinX', w / 2), ('TxtLocPinY', h / 2)):
                sh = set_cell(sh, k, repr(v))
            l, rgt, t, b = m.get('margins', (0, 0, 0, 0))
            for k, v in (('LeftMargin', l), ('RightMargin', rgt), ('TopMargin', t), ('BottomMargin', b)):
                sh = set_cell(sh, k, repr(v / PX))
            sh = set_cell(sh, 'VerticalAlign', {'top': 0, 'middle': 1, 'bottom': 2}.get(m.get('valign'), 1))
            sh = drop_cell(sh, 'TextBkgnd')
            if m.get('bold'):
                sh = re.sub(r'(<Section N="Character">.*?<Cell N="Style" V=)"(\d+)"',
                            lambda x: x.group(1) + '"%d"' % (int(x.group(2)) | 1), sh, count=1, flags=re.S)
            ha = {'left': 0, 'center': 1, 'right': 2}.get(m.get('align'), 1)
            if m.get('runs'):
                sh = rich_text(sh, m['runs'], ha, (m.get('alpha') or {}).get('text', 0))
            elif '<Section N="Paragraph">' in sh:
                sh = re.sub(r'<Cell N="HorzAlign" V="[^"]*"/>', '<Cell N="HorzAlign" V="%d"/>' % ha, sh)
            else:
                sh = sh.replace('<Text>', '<Section N="Paragraph"><Row IX="0"><Cell N="HorzAlign" V="%d"/></Row></Section><Text>' % ha, 1)
        sh = relativize(sh, w, h)
    al = m.get('alpha') or {}
    if al:
        if m['kind'] == 'e':
            if al.get('line'): sh = set_cell(sh, 'LineColorTrans', repr(al['line']))
        elif m.get('image'):
            if al.get('img'): sh = set_cell(sh, 'Transparency', repr(al['img']))
        elif m.get('owner') is not None:
            pass                     # label: its text goes into its shape
        else:
            if al.get('fill'):
                sh = set_cell(sh, 'FillForegndTrans', repr(al['fill']))
                sh = set_cell(sh, 'FillBkgndTrans', repr(al['fill']))
            if al.get('line'): sh = set_cell(sh, 'LineColorTrans', repr(al['line']))
        if m.get('textbg') and al.get('text'):
            # label background fades with its text
            sh = set_cell(sh, 'FillForegndTrans', repr(al['text']))
    out.append(sh)

# ---------------------------------------------------------------- labels -> text of their shape
def put_cell(sh, name, v, f=None):
    new = '<Cell N="%s" V="%r"%s/>' % (name, v, (' F="%s"' % f) if f else '')
    pat = r'<Cell N="%s" V="[^"]*"(?: F="[^"]*")?(?: U="[^"]*")?/>' % name
    if re.search(pat, sh):
        return re.sub(pat, lambda m: new, sh, count=1)
    return re.sub(r'(<Cell N="LocPinY" V="[^"]*"(?: F="[^"]*")?/>)', lambda m: m.group(1) + new, sh, count=1)

def box(sh):
    px, py, w, h, lx, ly = (cell(sh, n) for n in ('PinX', 'PinY', 'Width', 'Height', 'LocPinX', 'LocPinY'))
    return px - lx, py - ly, w, h

def insert_sections(sh, xml):
    """Sections (and Text) go before the geometry / picture data, as the exporter does."""
    for anchor in ('<Section N="Geometry"', '<ForeignData', '</Shape>'):
        i = sh.find(anchor)
        if i >= 0:
            return sh[:i] + xml + sh[i:]
    return sh

def absorb(osh, lsh, textbg=None, textbg_trans=0):
    """Make the text of label shape `lsh` the text of `osh`, in the same place."""
    ox, oy, ow, oh = box(osh)
    lx, ly, lw, lh = box(lsh)
    cx, cy = lx + lw / 2 - ox, ly + lh / 2 - oy
    parts = [re.search(r'<Section N="%s">.*?</Section>' % n, lsh, re.S) for n in ('Character', 'Paragraph')]
    text = re.search(r'<Text>.*?</Text>', lsh, re.S)
    if not text:
        return None
    for pat in (r'<Section N="Character">.*?</Section>', r'<Section N="Paragraph">.*?</Section>', r'<Text>.*?</Text>'):
        osh = re.sub(pat, '', osh, flags=re.S)
    osh = put_cell(osh, 'TxtPinX', cx, 'Width*0.5%+r' % round(cx - ow / 2, 10) if abs(cx - ow / 2) > 1e-6 else 'Width*0.5')
    if cy < 0:                       # below the shape: fixed distance under its bottom edge
        fy = '%r' % round(cy, 10)
    elif cy > oh:                    # above: fixed distance over its top edge
        fy = 'Height%+r' % round(cy - oh, 10)
    else:
        fy = 'Height*%r' % round(cy / oh, 10) if oh else None
    osh = put_cell(osh, 'TxtPinY', cy, fy)
    osh = put_cell(osh, 'TxtWidth', lw)
    osh = put_cell(osh, 'TxtHeight', lh)
    osh = put_cell(osh, 'TxtLocPinX', lw / 2, 'TxtWidth*0.5')
    osh = put_cell(osh, 'TxtLocPinY', lh / 2, 'TxtHeight*0.5')
    for n in ('LeftMargin', 'RightMargin', 'TopMargin', 'BottomMargin', 'VerticalAlign'):
        v = cell(lsh, n)
        if v is not None:
            osh = put_cell(osh, n, int(v) if n == 'VerticalAlign' else v)
    if textbg:
        osh = put_cell(osh, 'TextBkgnd', 0)
        osh = re.sub(r'<Cell N="TextBkgnd" V="0"/>', '<Cell N="TextBkgnd" V="%s"/>' % textbg, osh, count=1)
        osh = put_cell(osh, 'TextBkgndTrans', textbg_trans)
    return insert_sections(osh, ''.join(p.group(0) for p in parts if p) + text.group(0))

drop = set()
if not a.separate_labels:
    for i, m in enumerate(meta):
        j = m.get('owner')
        if m['kind'] == 'v' and j is not None and 0 <= j < len(out) and j not in drop and '<Text>' in out[i]:
            merged = absorb(out[j], out[i], m.get('textbg'), (m.get('alpha') or {}).get('text', 0))
            if merged:
                out[j] = merged
                drop.add(i)

# ---------------------------------------------------------------- glue arrows to their shapes
def sid(sh):
    return int(re.search(r'<Shape ID="(\d+)"', sh).group(1))

connects, conn_rows = [], {}
nglue = 0
if not a.no_glue:
    for i, m in enumerate(meta):
        if m['kind'] != 'e':
            continue
        glued = False
        for end, xc, yc, part, trig in (('src', 'BeginX', 'BeginY', 9, 'BegTrigger'), ('tgt', 'EndX', 'EndY', 12, 'EndTrigger')):
            j = m.get(end)
            if j is None or not (0 <= j < len(out)) or j in drop or meta[j]['kind'] != 'v':
                continue
            X, Y = cell(out[i], xc), cell(out[i], yc)
            if X is None or Y is None:
                continue
            ox, oy, ow, oh = box(out[j])
            x, y = X - ox, Y - oy
            rows = conn_rows.setdefault(j, [])
            n = len(rows) + 1
            rows.append('<Row IX="%d"><Cell N="X" V="%r" F="Width*%r"/><Cell N="Y" V="%r" F="Height*%r"/>'
                        '<Cell N="DirX" V="0"/><Cell N="DirY" V="0"/><Cell N="Type" V="0"/><Cell N="AutoGen" V="0"/></Row>'
                        % (n - 1, x, round(x / ow, 10) if ow else 0, y, round(y / oh, 10) if oh else 0))
            t = sid(out[j])
            out[i] = put_cell(out[i], xc, X, 'PAR(PNT(Sheet.%d!Connections.X%d,Sheet.%d!Connections.Y%d))' % (t, n, t, n))
            out[i] = put_cell(out[i], yc, Y, 'PAR(PNT(Sheet.%d!Connections.X%d,Sheet.%d!Connections.Y%d))' % (t, n, t, n))
            out[i] = put_cell(out[i], trig, 2, '_XFTRIGGER(Sheet.%d!EventXFMod)' % t)
            connects.append('<Connect FromSheet="%d" FromCell="%s" FromPart="%d" ToSheet="%d" ToCell="Connections.X%d" ToPart="%d"/>'
                            % (sid(out[i]), xc, part, t, n, 99 + n))
            glued = True
        if glued:
            # keep draw.io's route until a glued shape moves; then Visio reroutes it
            out[i] = put_cell(out[i], 'ConFixedCode', 1)
            nglue += 1
    for j, rows in conn_rows.items():
        out[j] = insert_sections(out[j], '<Section N="Connection">%s</Section>' % ''.join(rows))

# ---------------------------------------------------------------- groups
def num(sh, name):
    return cell(sh, name)

def setf(sh, name, v, f=None):
    pat = r'<Cell N="%s" V="[^"]*"(?: F="[^"]*")?(?: U="[^"]*")?/>' % name
    new = '<Cell N="%s" V="%r"%s/>' % (name, v, (' F="%s"' % f) if f else '')
    return re.sub(pat, new, sh, count=1) if re.search(pat, sh) else sh

next_id = max([int(i) for i in re.findall(r'<Shape ID="(\d+)"', page)] + [0]) + 1
members = {}
for i, m in enumerate(meta):
    if m.get('group') and not a.no_group and i not in drop:
        members.setdefault(m['group'], []).append(i)
final, done = [], set()
for i, sh in enumerate(out):
    if i in drop:
        continue
    g = meta[i].get('group')
    if not g or len(members.get(g, [])) < 2:
        final.append(sh); continue
    if g in done:
        continue
    done.add(g)
    kids = [out[k] for k in members[g]]
    boxes = []
    for k in kids:
        px, py, w, h, lx, ly = (num(k, n) for n in ('PinX', 'PinY', 'Width', 'Height', 'LocPinX', 'LocPinY'))
        boxes.append((px - lx, py - ly, px - lx + w, py - ly + h))
    gx0, gy0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    gw, gh = max(b[2] for b in boxes) - gx0, max(b[3] for b in boxes) - gy0
    gidn = next_id; next_id += 1
    S = 'Sheet.%d!' % gidn
    rel = []
    for k in kids:
        px, py, w, h = (num(k, n) for n in ('PinX', 'PinY', 'Width', 'Height'))
        npx, npy = px - gx0, py - gy0
        k = setf(k, 'PinX', npx, '%sWidth*%r' % (S, round(npx / gw, 10)))
        k = setf(k, 'Width', w, '%sWidth*%r' % (S, round(w / gw, 10)))
        if h <= 0.35 * gh:
            # cabeceras y nombres: altura fija, pegados al borde más cercano del grupo
            k = setf(k, 'Height', h)
            k = setf(k, 'PinY', npy, ('%sHeight-%r' % (S, round(gh - npy, 10))) if npy > gh / 2 else None)
        else:
            k = setf(k, 'PinY', npy, '%sHeight*%r' % (S, round(npy / gh, 10)))
            k = setf(k, 'Height', h, '%sHeight*%r' % (S, round(h / gh, 10)))
        rel.append(k)
    final.append('<Shape ID="%d" NameU="Group.%d" Name="Grupo.%d" Type="Group" LineStyle="0" FillStyle="0" TextStyle="0">'
                 '<Cell N="PinX" V="%r"/><Cell N="PinY" V="%r"/><Cell N="Width" V="%r"/><Cell N="Height" V="%r"/>'
                 '<Cell N="LocPinX" V="%r" F="Width*0.5"/><Cell N="LocPinY" V="%r" F="Height*0.5"/>'
                 '<Cell N="Angle" V="0"/><Cell N="SelectMode" V="1"/><Cell N="DisplayMode" V="2"/>'
                 '<Shapes>%s</Shapes></Shape>'
                 % (gidn, gidn, gidn, gx0 + gw / 2, gy0 + gh / 2, gw, gh, gw / 2, gh / 2, ''.join(rel)))

if shapes:
    start = page.index(shapes[0]); end = page.rindex(shapes[-1]) + len(shapes[-1])
    page = page[:start] + ''.join(final) + page[end:]
if connects:
    page = re.sub(r'<Connects>.*?</Connects>|<Connects/>', '', page, flags=re.S)
    k = page.rindex('</Shapes>') + len('</Shapes>')
    page = page[:k] + '<Connects>%s</Connects>' % ''.join(connects) + page[k:]
pages = re.sub(r'<Cell N="PageWidth" V="[^"]*"/>', '<Cell N="PageWidth" V="%r"/>' % PW, pages)
pages = re.sub(r'<Cell N="PageHeight" V="[^"]*"/>', '<Cell N="PageHeight" V="%r"/>' % PH, pages)

zout = zipfile.ZipFile(a.dst, 'w', zipfile.ZIP_DEFLATED)
for it in zin.infolist():
    data = zin.read(it.filename)
    if it.filename == 'visio/pages/page1.xml':
        data = page.encode('utf-8')
    elif it.filename == 'visio/pages/pages.xml':
        data = pages.encode('utf-8')
    zout.writestr(it, data)
zout.close()
print('página %.2f x %.2f in, contenido %.2f x %.2f in, %d formas, %d grupos, %d etiquetas en su forma, %d flechas pegadas'
      % (PW, PH, cw, ch, len(out) - len(drop), len(done), len(drop), nglue))
