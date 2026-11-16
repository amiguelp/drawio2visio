#!/usr/bin/env python3
"""Prepara (una sola vez) todo lo que necesita el conversor. Funciona en
Windows, macOS y Linux y solo usa los registros de npm y PyPI.

  - la app web de draw.io (para cargar el diagrama tal y como lo ves y obtener
    el trazado real de las flechas y la caja de cada texto)
  - el VsdxExport.js de draw.io 15.7 (las versiones actuales ya no exportan a
    Visio fuera de Confluence; ese exportador sigue funcionando en la app actual)
  - las librerías de iconos clásicas (img/lib: azure2, mscae, ibm...)
  - playwright-core para manejar un navegador sin interfaz. En Windows/macOS se
    usa Microsoft Edge o Google Chrome ya instalados; en Linux un Chromium que
    viene en un paquete npm.
  - Pillow (y cairosvg si se puede; en Windows no hace falta: los SVG se
    rasterizan con el propio navegador)

Uso:  python setup_env.py          (idempotente; imprime la carpeta de trabajo)
Env:  DRAWIO_VSDX_HOME  carpeta de trabajo (por defecto ~/.cache/drawio-vsdx)
"""
import base64, glob, html, json, os, re, shutil, subprocess, sys, zlib

W = os.environ.get('DRAWIO_VSDX_HOME') or os.path.join(os.path.expanduser('~'), '.cache', 'drawio-vsdx')
NODE_DIR = os.path.join(W, 'node')
WIN = os.name == 'nt'
LINUX = sys.platform.startswith('linux')


def sh(cmd, **kw):
    r = subprocess.run(cmd, shell=WIN, **kw)
    if r.returncode != 0:
        raise SystemExit('ERROR ejecutando: %s' % ' '.join(cmd))


def log(msg):
    print(msg, file=sys.stderr, flush=True)

# ---------------------------------------------------------------- stencils
def _num(v):
    return ('%.4f' % v).rstrip('0').rstrip('.') if isinstance(v, float) else str(v)


def _color(op, key):
    c = op.get(key)
    if isinstance(c, str) and (c.startswith('#') or c == 'none'):
        return c
    return op.get('default', 'none')


def _ops_xml(ops):
    out = []
    for op in ops:
        if isinstance(op, str):
            t = {'fillStroke': 'fillstroke', 'fill': 'fill', 'stroke': 'stroke', 'save': 'save',
                 'restore': 'restore'}.get(op)
            if t:
                out.append('<%s/>' % t)
            continue
        if 'path' in op:
            seg = []
            for c in op['path']:
                k, a = c[0], [_num(x) for x in c[1:]]
                if k == 'M': seg.append('<move x="%s" y="%s"/>' % tuple(a))
                elif k == 'L': seg.append('<line x="%s" y="%s"/>' % tuple(a))
                elif k == 'C': seg.append('<curve x1="%s" y1="%s" x2="%s" y2="%s" x3="%s" y3="%s"/>' % tuple(a))
                elif k == 'Q': seg.append('<quad x1="%s" y1="%s" x2="%s" y2="%s"/>' % tuple(a))
                elif k == 'A': seg.append('<arc rx="%s" ry="%s" x-axis-rotation="%s" large-arc-flag="%s" '
                                          'sweep-flag="%s" x="%s" y="%s"/>' % tuple(a))
                elif k == 'Z': seg.append('<close/>')
            out.append('<path>%s</path>' % ''.join(seg))
        elif 'rect' in op:
            out.append('<rect x="%s" y="%s" w="%s" h="%s"/>' % tuple(_num(x) for x in op['rect'][:4]))
        elif 'roundRect' in op:
            a = [_num(x) for x in op['roundRect']] + ['10']
            out.append('<roundrect x="%s" y="%s" w="%s" h="%s" arcsize="%s"/>' % tuple(a[:5]))
        elif 'ellipse' in op:
            out.append('<ellipse x="%s" y="%s" w="%s" h="%s"/>' % tuple(_num(x) for x in op['ellipse'][:4]))
        elif 'text' in op:
            t = op['text']
            out.append('<text str="%s" x="%s" y="%s" align="%s" valign="%s"/>' % (
                html.escape(str(t.get('str', '')), True), _num(t.get('x', 0)), _num(t.get('y', 0)),
                t.get('align', 'center'), t.get('valign', 'middle')))
        else:
            if 'fillColor' in op: out.append('<fillcolor color="%s"/>' % _color(op, 'fillColor'))
            if 'strokeColor' in op: out.append('<strokecolor color="%s"/>' % _color(op, 'strokeColor'))
            if 'fontColor' in op: out.append('<fontcolor color="%s"/>' % _color(op, 'fontColor'))
            if 'strokeWidth' in op: out.append('<strokewidth width="%s"/>' % _num(op['strokeWidth']))
            if 'alpha' in op: out.append('<alpha alpha="%s"/>' % _num(op['alpha']))
            if 'dashed' in op: out.append('<dashed dashed="%d"/>' % (1 if op['dashed'] else 0))
            if 'pattern' in op: out.append('<dashpattern pattern="%s"/>' % op['pattern'])
            if 'lineJoin' in op: out.append('<linejoin join="%s"/>' % op['lineJoin'])
            if 'lineCap' in op: out.append('<linecap cap="%s"/>' % op['lineCap'])
            if 'miterLimit' in op: out.append('<miterlimit limit="%s"/>' % _num(op['miterLimit']))
            if 'fontSize' in op: out.append('<fontsize size="%s"/>' % _num(op['fontSize']))
            if 'fontFamily' in op: out.append('<fontfamily family="%s"/>' % html.escape(op['fontFamily'], True))
            if 'fontStyle' in op: out.append('<fontstyle style="%s"/>' % _num(op['fontStyle']))
    return ''.join(out)


def _shape_xml(name, v):
    cons = ''.join('<constraint x="%s" y="%s" perimeter="%d" name="%s"/>' % (
        _num(c.get('x', 0)), _num(c.get('y', 0)), 1 if c.get('perimeter') else 0, html.escape(str(c.get('name', '')), True))
        for c in v.get('constraints') or [])
    return ('<shape name="%s" w="%s" h="%s" aspect="%s" strokewidth="%s">%s<foreground>%s</foreground></shape>'
            % (name, _num(v.get('width', 100)), _num(v.get('height', 100)), v.get('aspect', 'variable'),
               v.get('strokeWidth', 'inherit'), ('<connections>%s</connections>' % cons) if cons else '',
               _ops_xml(v.get('foreground') or [])))


def update_stencils(bundle, stencil_dir):
    """Añade a stencils/<grupo>.xml las formas que faltan, sacadas del paquete de
    stencils actuales de draw.io. No toca las que ya existen."""
    added = 0
    files = {}
    for f in sorted(glob.glob(os.path.join(bundle, '*.json'))):
        j = json.load(open(f, encoding='utf-8'))
        if 'data' not in j:
            continue
        for key, v in json.loads(zlib.decompress(base64.b64decode(j['data'])).decode('utf-8')).items():
            parts = key.split('.')
            if len(parts) < 2 or not isinstance(v, dict):
                continue
            files.setdefault(tuple(parts[:-1]), []).append((parts[-1], v))
    for parts, shapes in files.items():
        path = os.path.join(stencil_dir, *parts[:-1], parts[-1] + '.xml')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            txt = open(path, encoding='utf-8', errors='replace').read()
            have = {n.lower().replace(' ', '_') for n in re.findall(r'<shape\b[^>]*\bname="([^"]*)"', txt)}
        else:
            txt = '<shapes name="mxgraph.%s"></shapes>' % '.'.join(parts)
            have = set()
        new = [_shape_xml(n, v) for n, v in shapes if n.lower() not in have]
        if not new:
            continue
        i = txt.rfind('</shapes>')
        txt = txt[:i] + '\n'.join(new) + '\n' + txt[i:]
        with open(path, 'w', encoding='utf-8') as fo:
            fo.write(txt)
        added += len(new)
    log('  %d formas añadidas' % added)
    return added


def main():
    if not shutil.which('node') or not shutil.which('npm'):
        raise SystemExit('ERROR: falta Node.js (incluye npm). Instálalo desde https://nodejs.org (versión LTS) y vuelve a ejecutar.')
    os.makedirs(NODE_DIR, exist_ok=True)
    if not os.path.exists(os.path.join(NODE_DIR, 'package.json')):
        with open(os.path.join(NODE_DIR, 'package.json'), 'w', encoding='utf-8') as f:
            f.write('{"name":"drawio-vsdx-tools","version":"1.0.0","private":true}\n')

    pkgs = {'@zklogic/draw.io': '15.7.2-rc1', '@guowenzhang/dsh-drawioedit': '1.2.1', 'playwright-core': None,
            # solo se usan sus datos de stencils actuales de draw.io (resources/stencils)
            '@markdown-viewer/drawio2svg': '1.5.5'}
    if LINUX:
        pkgs['@sparticuz/chromium'] = None
    missing = [p for p in pkgs if not os.path.isdir(os.path.join(NODE_DIR, 'node_modules', *p.split('/')))]
    if missing:
        log('Instalando herramientas de draw.io desde npm (solo la primera vez, ~200 MB)...')
        spec = ['%s@%s' % (p, v) if v else p for p, v in pkgs.items()]
        sh(['npm', 'install', '--ignore-scripts', '--no-audit', '--no-fund', '--loglevel=error'] + spec,
           cwd=NODE_DIR, stdout=sys.stderr)

    try:
        import PIL  # noqa: F401
    except ImportError:
        log('Instalando Pillow...')
        sh([sys.executable, '-m', 'pip', 'install', '-q', 'pillow'], stdout=sys.stderr)
    if not WIN:
        try:
            import cairosvg  # noqa: F401
        except Exception:
            subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'cairosvg'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    old = os.path.join(NODE_DIR, 'node_modules', '@zklogic', 'draw.io', 'dist', 'mxgraph')
    new = os.path.join(NODE_DIR, 'node_modules', '@guowenzhang', 'dsh-drawioedit', 'editor')
    web = os.path.join(W, 'webroot')
    marker = os.path.join(web, 'js', 'vsdxold', 'VsdxExport.js')
    if not os.path.exists(marker):
        log('Preparando la app de draw.io...')
        shutil.rmtree(web, ignore_errors=True)
        shutil.copytree(new, web)
        shutil.copytree(os.path.join(old, 'img', 'lib'), os.path.join(web, 'img', 'lib'), dirs_exist_ok=True)
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        vsdx = os.path.join(old, 'grapheditor', 'diagramly', 'vsdx')
        for f in ('VsdxExport.js', 'mxVsdxCanvas2D.js'):
            shutil.copy(os.path.join(vsdx, f), os.path.dirname(marker))
    # librerías de stencils (Cisco, AWS, Citrix, P&ID, redes...): sin ellas draw.io
    # dibuja esos iconos como rectángulos rellenos
    if not os.path.exists(os.path.join(web, 'stencils', 'cisco', 'wireless.xml')):
        log('Copiando librerías de stencils...')
        shutil.copytree(os.path.join(old, 'stencils'), os.path.join(web, 'stencils'), dirs_exist_ok=True)
    bundle = os.path.join(NODE_DIR, 'node_modules', '@markdown-viewer', 'drawio2svg', 'resources', 'stencils')
    marker2 = os.path.join(web, 'stencils', '.actualizados')
    if os.path.isdir(bundle) and not os.path.exists(marker2):
        log('Añadiendo stencils recientes de draw.io...')
        n = update_stencils(bundle, os.path.join(web, 'stencils'))
        open(marker2, 'w').write('%d\n' % n)
    os.makedirs(os.path.join(web, '_hires', 'svg'), exist_ok=True)
    print(W)


if __name__ == '__main__':
    main()
