# draw.io → Visio sin descuadres

Convierte diagramas de draw.io / diagrams.net (`.drawio`) a Visio (`.vsdx`)
de forma que en Visio:

- **los textos quedan donde estaban** (nombres bajo los iconos, títulos de
  contenedores, etiquetas de flechas, textos con título + subtítulo);
- **las formas se pueden redimensionar** (rectángulos, iconos y contenedores);
- **las flechas mantienen exactamente su trazado**;
- **cada icono va agrupado con su nombre** y cada contenedor con su cabecera,
  así que se mueven juntos;
- los iconos SVG salen nítidos (≥256 px) y la fuente es Segoe UI.

## Requisitos (una sola vez)

1. **Python 3.9+** — <https://www.python.org> (marca *Add python.exe to PATH*).
2. **Node.js LTS** — <https://nodejs.org>.
3. **Microsoft Edge o Google Chrome** instalado (en Windows, Edge ya viene).

La primera ejecución descarga de npm la app de draw.io y su exportador
(~200 MB, a `%USERPROFILE%\.cache\drawio-vsdx`). Después ya no hace falta
conexión.

> **Proxy corporativo**: si `npm install` falla, configura el proxy:
> `npm config set proxy http://proxy:puerto` y
> `npm config set https-proxy http://proxy:puerto`.
> Como alternativa, copia la carpeta `.cache\drawio-vsdx` desde otro equipo
> donde ya se haya ejecutado.

## Uso

**Arrastrar y soltar**: arrastra uno o varios `.drawio` (o una carpeta) sobre
`convertir.bat`. El `.vsdx` se crea junto al original.

**Consola**:

```bat
convertir.bat C:\diagramas\arquitectura.drawio
convertir.bat C:\diagramas\arquitectura.drawio --page A3 --orientation landscape
convertir.bat C:\diagramas                       (todos los .drawio de la carpeta)
python convert.py arquitectura.drawio salida.vsdx --page-index 1
```

Si el diagrama tiene varias pestañas, se genera **un único `.vsdx` con una
página de Visio por pestaña**, con el mismo nombre y orden que en draw.io. Si
prefieres un archivo por pestaña (`arquitectura_NombrePestaña.vsdx`), usa
`--split`.

| Opción | Para qué |
|---|---|
| `--page A4\|A3\|Letter\|fit` | Tamaño de página (A4 por defecto; `fit` = ajustada al contenido) |
| `--orientation auto\|portrait\|landscape` | `auto` elige según la forma del diagrama |
| `--font "Segoe UI"` | Fuente de destino |
| `--page-index N` | Solo esa pestaña (0 = la primera) |
| `--split` | Un `.vsdx` por pestaña en vez de uno con todas |
| `--no-group` | No agrupar icono+nombre ni contenedor+cabecera |
| `--image-map map.json` | Sustituir iconos que no se encuentran (ver abajo) |
| `--preview` | PNG de comprobación (requiere LibreOffice) |
| `--workdir DIR` / `-v` | Conservar intermedios / ver cada paso |

## Por qué el exportador normal de draw.io falla y qué hace este

| Problema al exportar desde draw.io | Qué hace el script |
|---|---|
| Formas que no cambian de tamaño en Visio: la geometría se escribe con números fijos | Pone fórmulas relativas (`Width*…`, `Height*…`) en geometría, imágenes y bloque de texto |
| Textos desplazados: bloques de texto con desfases y sustitución de fuente | Bloque de texto = la forma exacta, alineación y márgenes explícitos, fuente Segoe UI |
| Nombres de iconos metidos dentro del icono y partidos en líneas | Un cuadro de texto por etiqueta, en la posición que tenía en draw.io y agrupado con el icono. Si la etiqueta tiene varias líneas con estilos distintos (título + subtítulo gris), van en el mismo cuadro, cada línea con su tamaño, color y negrita |
| Visio re-enruta las flechas | Flechas con los puntos exactos de draw.io y `ConFixedCode=2` |
| Cabeceras de contenedores perdidas, hijos descolocados | Contenedor = cuerpo + cabecera en coordenadas absolutas |
| Iconos SVG minúsculos y borrosos | Rasterizado a ≥256 px |

## A tener en cuenta

- Las flechas **no están pegadas** a las formas (por eso no se mueven), así
  que si desplazas un icono en Visio tendrás que reajustar su flecha.
- Si un aviso dice *imagen no encontrada localmente*, el icono venía de una
  URL externa. Pásale un sustituto con `--image-map`:
  ```json
  {"PowerBI.svg": "C:/iconos/power-bi.png"}
  ```
- *El contenido no cabe en A4*: prueba `--page A3` o `--orientation landscape`.
- Si no encuentra Edge/Chrome, indica la ruta:
  `set DRAWIO_BROWSER=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`
- La vista previa de LibreOffice dibuja a veces un marco fino alrededor de los
  iconos, y en etiquetas de varias líneas con tildes puede pintar la primera
  letra de la segunda línea con el tamaño de la primera. Son fallos de ese
  visor; en Visio no aparecen.

## Ficheros

| Fichero | Función |
|---|---|
| `convertir.bat` | Lanzador para Windows (arrastrar y soltar) |
| `convert.py` | Orquesta todo el proceso |
| `setup_env.py` | Descarga y prepara las herramientas (solo la primera vez) |
| `drawio_headless.mjs` | Abre draw.io en el navegador sin interfaz: mide, rasteriza y exporta |
| `flatten_for_visio.py` | Crea la copia «plana» del diagrama pensada para Visio |
| `post_vsdx.py` | Corrige el .vsdx: textos, tamaños, fórmulas, grupos, conectores y página |
| `merge_vsdx.py` | Une las páginas en un único .vsdx (también se puede usar suelto) |
