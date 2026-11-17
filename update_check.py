#!/usr/bin/env python3
"""Comprueba si hay una versión nueva del conversor en GitHub y ofrece actualizar.

Funciona cuando la carpeta es un clon de git (git clone ...): hace `git fetch`
con tus credenciales de GitHub, compara con la rama remota y, si hay cambios,
los enseña y pregunta si quieres actualizar (`git pull --ff-only`).

    python update_check.py            comprobar y preguntar
    python update_check.py --yes      actualizar sin preguntar
    python update_check.py --check    solo decir si hay versión nueva

Códigos de salida: 0 = sin cambios o no se actualizó, 10 = se ha actualizado,
1 = error. Nunca impide convertir: si no hay red o git, avisa y sigue.
"""
import os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TIMEOUT = 20  # segundos para hablar con GitHub


def git(*args, timeout=TIMEOUT):
    r = subprocess.run(['git', '-C', HERE] + list(args), capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, (r.stdout or '').strip(), (r.stderr or '').strip()


def say(msg=''):
    print(msg, flush=True)


def ask(question):
    if not sys.stdin or not sys.stdin.isatty():
        return False
    try:
        return input(question).strip().lower() in ('s', 'si', 'sí', 'y', 'yes')
    except EOFError:
        return False


def main(argv):
    yes = '--yes' in argv
    check_only = '--check' in argv
    if not shutil.which('git'):
        say('(Sin git instalado: no se comprueban actualizaciones.)')
        return 0
    rc, _, _ = git('rev-parse', '--is-inside-work-tree')
    if rc != 0:
        say('(Esta copia no es un clon de git: no se comprueban actualizaciones.\n'
            ' Para tenerlas: git clone https://github.com/amiguelp/drawio2visio)')
        return 0
    _, branch, _ = git('rev-parse', '--abbrev-ref', 'HEAD')
    _, upstream, _ = git('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
    if not upstream or upstream.startswith('fatal'):
        upstream = 'origin/' + (branch if branch and branch != 'HEAD' else 'main')
    try:
        rc, _, err = git('fetch', '--quiet', upstream.split('/')[0])
    except subprocess.TimeoutExpired:
        say('(No se pudo contactar con GitHub a tiempo: se sigue con la versión local.)')
        return 0
    if rc != 0:
        say('(No se pudo comprobar si hay actualizaciones: %s)' % (err.splitlines() or ['sin conexión'])[-1])
        return 0
    _, behind, _ = git('rev-list', '--count', 'HEAD..' + upstream)
    _, ahead, _ = git('rev-list', '--count', upstream + '..HEAD')
    behind, ahead = int(behind or 0), int(ahead or 0)
    _, here, _ = git('log', '-1', '--format=%h (%cd)', '--date=short')
    if behind == 0:
        if check_only:
            say('Versión al día: %s' % here)
        return 0

    _, log, _ = git('log', '--format=  · %cd  %s', '--date=short', 'HEAD..' + upstream)
    say('')
    say('Hay una versión nueva del conversor en GitHub (%d cambio%s):' % (behind, '' if behind == 1 else 's'))
    say(log)
    say('Tu versión: %s' % here)
    if check_only:
        return 0
    _, dirty, _ = git('status', '--porcelain', '--untracked-files=no')
    if dirty:
        say('\nTienes cambios propios en los scripts:\n' + dirty)
        say('No se actualiza automáticamente para no perderlos. Guárdalos (git stash) y actualiza con git pull.')
        return 0
    if ahead:
        say('\nTu copia tiene %d commit(s) que no están en GitHub; actualiza a mano con git pull.' % ahead)
        return 0
    if not (yes or ask('¿Actualizar ahora tu copia local? [s/N] ')):
        say('Sigues con la versión actual.\n')
        return 0
    try:
        rc, out, err = git('pull', '--ff-only', '--quiet', timeout=120)
    except subprocess.TimeoutExpired:
        say('La actualización tardó demasiado; se sigue con la versión actual.')
        return 1
    if rc != 0:
        say('No se pudo actualizar: %s' % (err or out))
        return 1
    _, now, _ = git('log', '-1', '--format=%h (%cd)', '--date=short')
    say('Actualizado a %s.\n' % now)
    return 10


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
