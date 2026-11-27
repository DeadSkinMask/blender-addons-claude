# blender-addons-claude

Addons de Blender 5.2, un proyecto por carpeta (independientes entre sí).

| Carpeta | Qué es |
|---|---|
| `Godot Level Bridge/` | Extensión que exporta niveles de Blender a Godot 4.7 (geometría, placeholders, colisores primitivos, áreas, paths). Formato en `FORMAT.md`; el lado Godot vive en el juego Elegía (`addons/level_bridge/`). |
| `Godot MultiMesh Scatter/` | Addon + loader de Godot para exportar instancias de Geometry Nodes como MultiMesh. |
| `Sculpt Brush Shelf/` | Extensión con una lista corta y configurable de pinceles de Sculpt en el sidebar. |
| `Inertial Bone/` | Extensión con la vista previa en vivo del hueso inercial de Godot (`InertialBone3D`): masa con muelle e inercia que se desplaza (no rota), mismos controles y valores que en Godot, con interruptor para apagarla. |

| `PolyQuilt_Fork/` | Fork propio de PolyQuilt (retopología): el pincel Relax respeta el grupo `vertex_pinned`, pinceles Pin/Unpin, resaltado cian de los vértices pinned, autoguardado de respaldo y varias correcciones de cierres. Id `polyquilt_luis_fork`. |
| `RetopoFlow Fork/` | Fork propio de RetopoFlow 3. Id `retopoflow_luis_fork`. |

Cada proyecto incluye su ZIP instalable, generado con su `build_zip.sh`. Guía para agentes: `CLAUDE.md`.

## Instalar y actualizar los addons (`update_addons.py`)

Un único script en Python (solo librería estándar, sin PowerShell) instala o actualiza todas las extensiones de este repo usando la línea de comandos de Blender (`blender --command extension ...`), sin abrir la interfaz. Sirve en Windows y en Linux.

```bash
python update_addons.py --list                # estado de cada addon: versión del ZIP, versión instalada
python update_addons.py                       # actualiza los que ya están instalados
python update_addons.py --all                 # además instala los que faltan (menos los opcionales)
python update_addons.py polyquilt retopo      # solo esos (por id o nombre), instalados o no
python update_addons.py sculpt --force        # reinstala aunque la versión sea igual o menor
python update_addons.py --dry-run             # solo imprime los comandos de Blender
python update_addons.py --blender "C:\Program Files\Blender Foundation\Blender 5.3\blender.exe"
```

**Cómo decide.** Recorre el repo, abre cada ZIP que lleve `blender_manifest.toml` y se queda con el más nuevo de cada `id` (de RustRemesher elige el build del sistema operativo actual, el que trae el motor). Compara esa versión con el `blender_manifest.toml` ya instalado; la carpeta de extensiones se la pregunta a Blender, así que respeta `BLENDER_USER_EXTENSIONS` y las instalaciones portables. Estados: `not installed`, `up to date`, `update available`, `installed is NEWER` (en ese caso no toca nada salvo `--force`).

**Qué hace por cada addon.** Quita la versión anterior (instalar encima ha dejado código viejo corriendo), instala el ZIP en el repositorio `user_default`, lo activa y vuelve a leer el manifest para confirmar la versión. Si uno falla sigue con los demás y lo resume al final. Los ajustes del addon se conservan mientras no cambie su `id`.

**Qué Blender usa.** `--blender`, si no la variable `BLENDER_EXE`, si no la carpeta `Blender *` más nueva de `Program Files\Blender Foundation` (Windows), si no `blender` del PATH.

**Opcionales.** `sculpt_brush_shelf` y `rustremesher` están marcados como opcionales (constante `OPTIONAL` al inicio del script) porque solo se quieren en algunas PC: `--all` no los instala, pero se actualizan si ya están instalados y se instalan si los nombras. `--all --include-optional` los incluye.

**Forks propios.** `polyquilt_luis_fork` y `retopoflow_luis_fork` tienen id propio para que Blender no los compare con los originales de extensions.blender.org (no ofrece "Update" que los pise ni los marca como desactualizados). Al actualizar, el script quita también los ids antiguos (`PolyQuilt_Fork`, `retopoflow`) si estaban instalados en `user_default`; el cambio de id reinicia los ajustes del addon una sola vez. Si además tienes instalado el original desde extensions.blender.org, el script avisa: desinstálalo desde Preferences.

**Para publicar una versión nueva.** Construye el ZIP del proyecto (su `build_zip.sh`, o `blender --command extension build --source-dir <carpeta> --output-dir .` en los forks), sube la versión en su `blender_manifest.toml`, haz commit del ZIP y, en la otra PC, `git pull` y `python update_addons.py`. `Godot MultiMesh Scatter` no está en la lista: es un `.py` suelto sin manifest que se instala con *Install from Disk*.
