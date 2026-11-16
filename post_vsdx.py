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
import argparse, json, re, zipfile

ap = argparse.ArgumentParser()
ap.add_argument('src'); ap.add_argument('meta'); ap.add_argument('dst')
ap.add_argument('--page', default='A4')
ap.add_argument('--orientation', default='auto')
ap.add_argument('--no-group', action='store_true')
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
    return re.sub(r'(<Cell N="LocPinY" V="[^"]*"/>)', lambda m: m.group(1) + new, sh, count=1)

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

# draw.io px per inch as written by VsdxExport (1 px = 0.254 mm)
PX = 600 / 5.905511811023622

xs, ys = [], []
for sh, m in zip(shapes, meta):
    if m['kind'] == 'e':
        xs += [cell(sh, 'BeginX'), cell(sh, 'EndX')]; ys += [cell(sh, 'BeginY'), cell(sh, 'EndY')]
    else:
        px, py, w, h, lx, ly = (cell(sh, k) for k in ('PinX', 'PinY', 'Width', 'Height', 'LocPinX', 'LocPinY'))
        xs += [px - lx, px - lx + w]; ys += [py - ly, py - ly + h]
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
            if '<Section N="Paragraph">' in sh:
                sh = re.sub(r'<Cell N="HorzAlign" V="[^"]*"/>', '<Cell N="HorzAlign" V="%d"/>' % ha, sh)
            else:
                sh = sh.replace('<Text>', '<Section N="Paragraph"><Row IX="0"><Cell N="HorzAlign" V="%d"/></Row></Section><Text>' % ha, 1)
        sh = relativize(sh, w, h)
    out.append(sh)

# ---------------------------------------------------------------- groups
def num(sh, name):
    return cell(sh, name)

def setf(sh, name, v, f=None):
    pat = r'<Cell N="%s" V="[^"]*"(?: F="[^"]*")?(?: U="[^"]*")?/>' % name
    new = '<Cell N="%s" V="%r"%s/>' % (name, v, (' F="%s"' % f) if f else '')
    return re.sub(pat, new, sh, count=1) if re.search(pat, sh) else sh

next_id = max(int(i) for i in re.findall(r'<Shape ID="(\d+)"', page)) + 1
members = {}
for i, m in enumerate(meta):
    if m.get('group') and not a.no_group:
        members.setdefault(m['group'], []).append(i)
final, done = [], set()
for i, sh in enumerate(out):
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

start = page.index(shapes[0]); end = page.rindex(shapes[-1]) + len(shapes[-1])
page = page[:start] + ''.join(final) + page[end:]
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
print('página %.2f x %.2f in, contenido %.2f x %.2f in, %d formas, %d grupos' % (PW, PH, cw, ch, len(out), len(done)))
