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
import os, shutil, subprocess, sys

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


def main():
    if not shutil.which('node') or not shutil.which('npm'):
        raise SystemExit('ERROR: falta Node.js (incluye npm). Instálalo desde https://nodejs.org (versión LTS) y vuelve a ejecutar.')
    os.makedirs(NODE_DIR, exist_ok=True)
    if not os.path.exists(os.path.join(NODE_DIR, 'package.json')):
        with open(os.path.join(NODE_DIR, 'package.json'), 'w', encoding='utf-8') as f:
            f.write('{"name":"drawio-vsdx-tools","version":"1.0.0","private":true}\n')

    pkgs = {'@zklogic/draw.io': '15.7.2-rc1', '@guowenzhang/dsh-drawioedit': '1.2.1', 'playwright-core': None}
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
    os.makedirs(os.path.join(web, '_hires', 'svg'), exist_ok=True)
    print(W)


if __name__ == '__main__':
    main()
