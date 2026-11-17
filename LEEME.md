# draw.io → Visio sin descuadres

Convierte diagramas de draw.io / diagrams.net (`.drawio`) a Visio (`.vsdx`)
de forma que en Visio:

- **los textos quedan donde estaban** (nombres bajo los iconos, títulos de
  contenedores, etiquetas de flechas, textos con título + subtítulo);
- **las formas se pueden redimensionar** (rectángulos, iconos y contenedores);
- **las flechas mantienen exactamente su trazado y están pegadas a sus
  formas**: al mover una forma, la flecha la sigue;
- **las etiquetas son el texto de su forma** (el nombre de un icono es el
  texto del icono, colocado debajo como en draw.io), así que se mueven con
  ella y se editan haciendo doble clic en la forma;
- cada contenedor va agrupado con su cabecera;
- las tablas HTML dentro de una etiqueta se reconstruyen con formas de Visio;
- los iconos SVG salen nítidos (≥256 px) y se conserva la fuente original
  cuando es estándar de Windows (Arial, Calibri…).

## Requisitos (una sola vez)

1. **Python 3.9+** — <https://www.python.org> (marca *Add python.exe to PATH*).
2. **Node.js LTS** — <https://nodejs.org>.
3. **Microsoft Edge o Google Chrome** instalado (en Windows, Edge ya viene).

La primera ejecución descarga de npm la app de draw.io, su exportador y las
librerías de iconos actuales (~210 MB, a `%USERPROFILE%\.cache\drawio-vsdx`). Después ya no hace falta
conexión.

> **Proxy corporativo**: si `npm install` falla, configura el proxy:
> `npm config set proxy http://proxy:puerto` y
> `npm config set https-proxy http://proxy:puerto`.
> Como alternativa, copia la carpeta `.cache\drawio-vsdx` desde otro equipo
> donde ya se haya ejecutado.

## Actualizaciones

Si tu copia es un clon de git (`git clone https://github.com/amiguelp/drawio2visio`),
cada vez que conviertes se comprueba si hay una versión nueva en GitHub. Si la
hay, se enseñan los cambios y se pregunta si quieres actualizar tu copia; si
dices que sí, se actualiza (`git pull`) y la conversión sigue con la versión
nueva.

- Comprobar a mano: `python update_check.py --check`
- Actualizar sin preguntar: `python update_check.py --yes`
- Desactivar la comprobación: `--no-update-check` o la variable de entorno
  `DRAWIO2VISIO_NO_UPDATE=1`.
- Si has modificado los scripts, no se actualiza solo (para no perder tus
  cambios): te lo indica y puedes hacer `git stash` y `git pull`.
- Sin conexión, sin git o en una copia descargada como ZIP, simplemente avisa
  y convierte con la versión que tienes.

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

Si el `.vsdx` ya existe no se sobrescribe: se crea `nombre_2.vsdx`,
`nombre_3.vsdx`… y se avisa. Así no falla aunque el anterior esté abierto en
Visio. Con `--overwrite` se reemplaza, salvo que esté abierto.

Si el diagrama tiene varias pestañas, se genera **un único `.vsdx` con una
página de Visio por pestaña**, con el mismo nombre y orden que en draw.io. Si
prefieres un archivo por pestaña (`arquitectura_NombrePestaña.vsdx`), usa
`--split`.

| Opción | Para qué |
|---|---|
| `--page A4\|A3\|Letter\|fit` | Tamaño de página (A4 por defecto; `fit` = ajustada al contenido) |
| `--orientation auto\|portrait\|landscape` | `auto` elige según la forma del diagrama |
| `--font "Segoe UI"` | Fuente para los textos cuya fuente no viene con Windows (Helvetica, fuentes web…) |
| `--page-index N` | Solo esa pestaña (0 = la primera) |
| `--split` | Un `.vsdx` por pestaña en vez de uno con todas |
| `--overwrite` | Sobrescribir el `.vsdx` si ya existe (si está abierto en Visio, se guarda con sufijo) |
| `--no-group` | No agrupar contenedor+cabecera |
| `--no-glue` | Flechas sin pegar: no cambian nunca, pero no siguen a las formas |
| `--separate-labels` | Etiquetas como cuadros de texto aparte, agrupados con su forma |
| `--image-map map.json` | Sustituir iconos que no se encuentran (ver abajo) |
| `--preview` | PNG de comprobación (requiere LibreOffice) |
| `--workdir DIR` / `-v` | Conservar intermedios / ver cada paso |

## Por qué el exportador normal de draw.io falla y qué hace este

| Problema al exportar desde draw.io | Qué hace el script |
|---|---|
| Formas que no cambian de tamaño en Visio: la geometría se escribe con números fijos | Pone fórmulas relativas (`Width*…`, `Height*…`) en geometría, imágenes y bloque de texto |
| Textos desplazados: bloques de texto con desfases y sustitución de fuente | Bloque de texto = la forma exacta, alineación y márgenes explícitos. Se mantienen las fuentes estándar de Windows y el tamaño exacto (el exportador lo reducía un 3 %); las demás pasan a `--font` |
| Tablas HTML dentro de una etiqueta (Visio no admite tablas en un texto): todo el texto salía en una línea | Se miden tal como las maqueta draw.io y se rehacen con un cuadro de texto por celda, los fondos de fila/celda y las líneas de borde, agrupados con su cuadro. Los saltos de línea de cada celda son los de draw.io |
| Nombres de iconos metidos dentro del icono y partidos en líneas | Un cuadro de texto por etiqueta, en la posición que tenía en draw.io y agrupado con el icono. Si la etiqueta tiene varias líneas con estilos distintos (título + subtítulo gris), van en el mismo cuadro, cada línea con su tamaño, color y negrita |
| Visio re-enruta las flechas al abrir o se descuelgan | Flechas con los puntos exactos de draw.io, pegadas a un punto de conexión creado justo donde llegan. Visio conserva el trazado hasta que mueves una de sus formas |
| Cabeceras de contenedores perdidas, hijos descolocados | Contenedor = cuerpo + cabecera en coordenadas absolutas |
| Colores de texto perdidos o todo en negrita en etiquetas con formato (`<b>`, `<font color>`, `<span style>`) | El formato se toma de cómo lo pinta draw.io (color, tamaño, negrita, cursiva y subrayado de cada trozo de texto, aunque cambie a mitad de línea) |
| Textos sueltos sobre una flecha que quedan debajo y sin fondo | Todo se escribe en el mismo orden de apilado que en draw.io y los textos conservan su fondo (`labelBackgroundColor` o relleno) |
| Transparencias (`opacity`, `fillOpacity`, `strokeOpacity`, `textOpacity`) perdidas | Se pasan a la transparencia de relleno, línea, imagen y texto de Visio |
| Esquinas redondeadas mucho más grandes en Visio: el exportador pone el radio al 10 % del ancho | Radio calculado como draw.io (15 % del lado menor, o `arcSize` / `absoluteArcSize` de la forma; flechas redondeadas incluidas) |
| Iconos SVG minúsculos y borrosos | Rasterizado a ≥256 px |
| Iconos de librerías (Cisco, AWS, Citrix, redes, P&ID…) convertidos en cuadrados de color | draw.io dibuja cada icono y se inserta como imagen en alta resolución (su nombre sigue siendo texto editable). Se añaden las librerías de stencils actuales de draw.io |
| Imágenes pegadas dentro de un texto (p. ej. un diagrama pegado como imagen) | Se insertan como imagen; antes la pestaña quedaba vacía y daba error |

## A tener en cuenta

- Al mover una forma, Visio vuelve a trazar sus flechas a su manera
  (ángulos rectos). Si prefieres que nunca cambien, usa `--no-glue`.
- Las etiquetas de las flechas siguen siendo cuadros de texto aparte.
- Si un aviso dice *imagen no encontrada localmente*, el icono venía de una
  URL externa. Pásale un sustituto con `--image-map`:
  ```json
  {"PowerBI.svg": "C:/iconos/power-bi.png"}
  ```
- Si un icono sale como rectángulo de color, comprueba cómo se ve en draw.io:
  los diagramas generados por IA a veces usan nombres de iconos que no existen
  (`mxgraph.pid.misc.plc`, por ejemplo) y draw.io también los pinta como
  rectángulos.
- Pestañas sin nada exportable salen como página en blanco con un aviso.
- *El contenido no cabe en A4*: prueba `--page A3` o `--orientation landscape`.
- Si no encuentra Edge/Chrome, indica la ruta:
  `set DRAWIO_BROWSER=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`
- La vista previa de LibreOffice no dibuja las esquinas redondeadas ni las
  transparencias de Visio; en Visio sí aparecen.
- El resaltado de fondo de una palabra suelta dentro de un texto no se
  traslada (Visio solo admite un fondo para todo el texto de la forma).
- Para comparar, `node drawio_headless.mjs png diagrama.drawio ref.png --page N`
  genera una imagen de la pestaña tal como la pinta draw.io.
- Los iconos conservan su fondo transparente. La vista previa de LibreOffice
  pinta un recuadro (azul o con marco fino) detrás de ellos; en Visio no
  aparece. En etiquetas de varias líneas con tildes puede pintar la primera
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
| `update_check.py` | Comprueba si hay versión nueva en GitHub y actualiza la copia local |
