@echo off
rem ------------------------------------------------------------------
rem  draw.io -> Visio  (arrastra uno o varios .drawio o carpetas encima)
rem  Uso en consola:  convertir.bat diagrama.drawio [opciones]
rem  Opciones: --page A3 --orientation landscape --preview --split --no-group ...
rem ------------------------------------------------------------------
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set "HERE=%~dp0"

where python >nul 2>nul && (set "PY=python") || (
  where py >nul 2>nul && (set "PY=py -3") || (
    echo ERROR: no se encuentra Python 3. Instalalo desde https://www.python.org ^(marca "Add to PATH"^).
    pause & exit /b 1))
where node >nul 2>nul || (
  echo ERROR: no se encuentra Node.js. Instalalo desde https://nodejs.org ^(version LTS^).
  pause & exit /b 1)

if "%~1"=="" (
  echo Arrastra un archivo .drawio ^(o una carpeta^) sobre convertir.bat
  pause & exit /b 0)

rem Comprobar una sola vez si hay versión nueva en GitHub. Va entre paréntesis para
rem que cmd lo lea entero antes de ejecutarlo: si la actualización cambia este .bat,
rem se relanza la versión nueva en lugar de seguir leyendo el fichero modificado.
if not defined D2V_ACTUALIZADO (
  %PY% "%HERE%update_check.py"
  if errorlevel 10 (
    set "D2V_ACTUALIZADO=1"
    call "%~f0" %*
    exit /b
  )
)

rem Con opciones (--algo) se pasa todo tal cual a convert.py
echo %* | findstr /C:"--" >nul && goto passthru

rem Arrastrar y soltar: cada argumento es un fichero o carpeta
set "RC=0"
:loop
if "%~1"=="" goto end
%PY% "%HERE%convert.py" "%~1" --no-update-check
if errorlevel 1 set "RC=1"
shift
goto loop

:passthru
%PY% "%HERE%convert.py" %* --no-update-check
set "RC=%errorlevel%"

:end
echo.
pause
exit /b %RC%
