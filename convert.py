#!/usr/bin/env python3
"""draw.io -> Visio (.vsdx) sin que se muevan flechas ni textos.

  python convert.py diagrama.drawio                     -> diagrama.vsdx (todas las páginas)
  python convert.py diagrama.drawio salida.vsdx --page-index 2
  python convert.py C:\\carpeta\\con\\drawios             -> un .vsdx por diagrama y página

Opciones:
  --page A4|A3|Letter|fit          tamaño de papel (por defecto A4)
  --orientation auto|portrait|landscape
  --font "Segoe UI"                fuente de destino (instalada en todo Windows)
  --image-map map.json             sustituir iconos que no se encuentran
  --page-index N                   exportar solo esa página (0 = la primera)
  --split                          un .vsdx por pestaña en vez de uno con todas
  --no-group                       no agrupar contenedor+cabecera
  --no-glue                        flechas sin pegar a las formas (no se mueven nunca)
  --separate-labels                etiquetas como cuadros de texto aparte (agrupados)
  --preview                        genera un PNG de comprobación (requiere LibreOffice)
  --workdir DIR                    conservar ficheros intermedios
  --no-update-check                no comprobar si hay versión nueva en GitHub

Si el .drawio tiene varias pestañas, se genera UN solo .vsdx con una página de
Visio por pestaña (con el mismo nombre). Con --split, un .vsdx por pestaña.

Pasos (cada uno es un script independiente):
  1. setup_env.py           herramientas desde npm/PyPI (caché en ~/.cache/drawio-vsdx)
  2. drawio_headless dump   draw.io real calcula trazados de flechas y cajas de texto
  3. flatten_for_visio      copia plana pensada para Visio
     (+ drawio_headless raster si no hay cairosvg, típico en Windows)
  4. drawio_headless export el VsdxExport de draw.io sobre esa copia
  5. post_vsdx              ajusta textos, conectores, imágenes y página
  6. vista previa opcional  LibreOffice -> PNG
"""
import argparse, glob, html, os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WIN = os.name == 'nt'

ap = argparse.ArgumentParser(description='draw.io -> Visio (.vsdx) conservando flechas y textos')
ap.add_argument('input', help='fichero .drawio/.xml o carpeta con varios')
ap.add_argument('output', nargs='?', help='.vsdx de salida (por defecto, junto al original)')
ap.add_argument('--page', default='A4')
ap.add_argument('--orientation', default='auto')
ap.add_argument('--font', default='Segoe UI')
ap.add_argument('--image-map', default=None)
ap.add_argument('--page-index', default=None, type=int)
ap.add_argument('--preview', action='store_true')
ap.add_argument('--split', action='store_true', help='un .vsdx por pestaña')
ap.add_argument('--no-glue', action='store_true', help='no pegar las flechas a las formas')
ap.add_argument('--separate-labels', action='store_true', help='etiquetas como cuadros de texto aparte')
ap.add_argument('--no-group', action='store_true', help='no agrupar icono+nombre ni contenedor+cabecera')
ap.add_argument('--workdir', default=None)
ap.add_argument('--verbose', '-v', action='store_true')
ap.add_argument('--no-update-check', action='store_true', help='no comprobar si hay versión nueva en GitHub')
a = ap.parse_args()

# ---------------------------------------------------------------- versión nueva en GitHub
if not a.no_update_check and not os.environ.get('DRAWIO2VISIO_NO_UPDATE'):
    try:
        rc = subprocess.run([sys.executable, os.path.join(HERE, 'update_check.py')]).returncode
    except Exception:
        rc = 0
    if rc == 10:   # actualizado: se vuelve a lanzar con el código nuevo
        sys.stdout.flush()
        sys.exit(subprocess.run([sys.executable, os.path.abspath(__file__)] + sys.argv[1:] + ['--no-update-check']).returncode)

ENV = dict(os.environ, PYTHONIOENCODING='utf-8')


def run(cmd, quiet=False, **kw):
    if a.verbose:
        print('$', ' '.join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace', env=ENV, **kw)
    out = (r.stdout or '').strip()
    if r.returncode != 0:
        if out:
            print(out)
        print((r.stderr or '').strip()[-3000:], file=sys.stderr)
        raise RuntimeError('falló: %s' % os.path.basename(cmd[1] if len(cmd) > 1 else cmd[0]))
    if out and (a.verbose or not quiet):
        for line in out.splitlines():
            if a.verbose or line.startswith('AVISO') or line.startswith('página'):
                print('   ' + line)
    return r


def page_names(path):
    """Nombres de las páginas del .drawio (sin descomprimir el contenido)."""
    try:
        txt = open(path, encoding='utf-8', errors='replace').read()
    except OSError:
        return ['']
    names = [html.unescape(n) for n in re.findall(r'<diagram\b[^>]*?\bname="([^"]*)"', txt)]
    n = len(re.findall(r'<diagram\b', txt))
    if n <= 1:
        return ['']
    return (names + [''] * n)[:n]


def safe(name):
    return re.sub(r'[\\/:*?"<>|]+', '_', name).strip() or 'pagina'


# ---------------------------------------------------------------- herramientas
print('Preparando herramientas...', flush=True)
home = run([sys.executable, os.path.join(HERE, 'setup_env.py')], quiet=True).stdout.strip().splitlines()[-1]
ENV['DRAWIO_VSDX_HOME'] = home
NODE = shutil.which('node') or 'node'
HEADLESS = os.path.join(HERE, 'drawio_headless.mjs')


def convert_page(src, dst, page_index, wd, prev_name=None):
    os.makedirs(wd, exist_ok=True)
    dump, flat, raw = (os.path.join(wd, n) for n in ('dump.json', 'flat.drawio', 'raw.vsdx'))
    run([NODE, HEADLESS, 'dump', os.path.abspath(src), dump, '--page', str(page_index)], quiet=True)
    fl = [sys.executable, os.path.join(HERE, 'flatten_for_visio.py'), dump, flat, '--font', a.font,
          '--webroot', os.path.join(home, 'webroot')]
    if a.image_map:
        fl += ['--image-map', a.image_map]
    pend = flat + '.pending.json'
    r = run(fl, quiet=True)
    if os.path.exists(pend):          # sin cairosvg: el navegador rasteriza los SVG y se repite
        run([NODE, HEADLESS, 'raster', pend, '-'], quiet=True)
        r = run(fl, quiet=True)
    for line in r.stdout.splitlines():
        if line.startswith('AVISO'):
            print('   ' + line)
    run([NODE, HEADLESS, 'export', flat, raw], quiet=True)
    run([sys.executable, os.path.join(HERE, 'post_vsdx.py'), raw, flat + '.meta.json', os.path.abspath(dst),
         '--page', a.page, '--orientation', a.orientation] + (['--no-group'] if a.no_group else [])
         + (['--no-glue'] if a.no_glue else []) + (['--separate-labels'] if a.separate_labels else []))
    if a.preview:
        preview(dst, wd, prev_name)


def find_soffice():
    for n in ('soffice', 'libreoffice'):
        if shutil.which(n):
            return shutil.which(n)
    for p in (r'C:\Program Files\LibreOffice\program\soffice.exe', r'C:\Program Files (x86)\LibreOffice\program\soffice.exe',
              '/Applications/LibreOffice.app/Contents/MacOS/soffice'):
        if os.path.exists(p):
            return p
    return None


def preview(dst, wd, target=None):
    so = find_soffice()
    if not so:
        print('   AVISO: sin LibreOffice no hay vista previa (no afecta al .vsdx)')
        return
    stem = os.path.splitext(os.path.basename(dst))[0]
    target = target or os.path.splitext(os.path.abspath(dst))[0] + '_preview.png'
    try:
        if shutil.which('pdftoppm'):
            run([so, '--headless', '--convert-to', 'pdf', '--outdir', wd, os.path.abspath(dst)], quiet=True)
            run(['pdftoppm', '-r', '110', '-png', '-singlefile', os.path.join(wd, stem + '.pdf'),
                 os.path.splitext(target)[0]], quiet=True)
        else:
            run([so, '--headless', '--convert-to', 'png', '--outdir', wd, os.path.abspath(dst)], quiet=True)
            shutil.move(os.path.join(wd, stem + '.png'), target)
        print('   vista previa:', target)
    except Exception as e:
        print('   AVISO: no se pudo generar la vista previa (%s)' % e)


# ---------------------------------------------------------------- entradas
if os.path.isdir(a.input):
    files = sorted(f for ext in ('*.drawio', '*.drawio.xml', '*.xml') for f in glob.glob(os.path.join(a.input, ext)))
    if a.output:
        os.makedirs(a.output, exist_ok=True)
else:
    files = [a.input]
if not files:
    raise SystemExit('No hay ficheros .drawio en %s' % a.input)

base_wd = a.workdir or tempfile.mkdtemp(prefix='drawio2vsdx_')
ok, fail = [], []
for f in files:
    stem = re.sub(r'(\.drawio)?(\.xml)?$', '', os.path.basename(f), flags=re.I)
    if os.path.isdir(a.input):
        outdir = a.output or os.path.dirname(os.path.abspath(f))
        single = None
    else:
        outdir = os.path.dirname(os.path.abspath(a.output)) if a.output else os.path.dirname(os.path.abspath(f))
        single = a.output
    names = page_names(f)
    idxs = [a.page_index] if a.page_index is not None else list(range(len(names)))
    multi = len(names) > 1
    combine = multi and len(idxs) > 1 and not a.split
    target = single or os.path.join(outdir, stem + '.vsdx')
    parts = []
    for i in idxs:
        pname = names[i] if i < len(names) and names[i] else 'Página %d' % (i + 1)
        pdir = os.path.join(base_wd, '%s_p%d' % (safe(stem), i))
        if combine:
            dst = os.path.join(pdir, 'pagina.vsdx')
        elif multi and not (single and len(idxs) == 1):
            dst = os.path.join(outdir, '%s_%s.vsdx' % (stem, safe(pname)))
        else:
            dst = target
        prev = os.path.join(outdir, '%s_%s_preview.png' % (stem, safe(pname))) if multi else None
        print('-> %s%s' % (os.path.basename(f), (' [pestaña %d: %s]' % (i + 1, pname)) if multi else ''), flush=True)
        try:
            convert_page(f, dst, i, pdir, prev)
            parts.append((dst, pname))
            if not combine:
                print('   OK:', dst)
                ok.append(dst)
        except Exception as e:
            print('   ERROR:', e)
            fail.append('%s (pestaña %d)' % (f, i + 1))
    if combine and parts:
        try:
            run([sys.executable, os.path.join(HERE, 'merge_vsdx.py'), target] + [x for p in parts for x in p], quiet=True)
            print('   OK: %s (%d páginas)' % (target, len(parts)))
            ok.append(target)
        except Exception as e:
            print('   ERROR al unir las páginas:', e)
            fail.append(f)

print('\n%d convertido(s), %d con error.' % (len(ok), len(fail)))
if fail:
    print('Con error:\n  ' + '\n  '.join(fail))
if a.workdir or a.verbose:
    print('intermedios en:', base_wd)
else:
    shutil.rmtree(base_wd, ignore_errors=True)
sys.exit(1 if fail else 0)
