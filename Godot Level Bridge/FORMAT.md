# Formato `gbridge-level` v1

Contrato entre el addon de Blender (`godot_level_bridge/`, escritor) y el plugin de Godot (`addons/level_bridge/` del juego, lector). Cualquier cambio se hace en los dos lados a la vez, junto con sus tests, y se regeneran los fixtures (`UPDATE_FIXTURES=1 ./run_tests.sh`).

Estado: **fases B0–B4** (geometría estática, placeholders, marcadores, colisores, áreas, paths, instancias de Geometry Nodes como MultiMesh, assets por colección instanciada). Lo marcado *(fase N)* está diseñado en `ANALISIS_Level_Bridge.md` §5 y se fija aquí al implementarse.

## 1. Archivos

| Archivo | Contenido |
|---|---|
| `<salida>/<nivel>.level.json` | Manifiesto del nivel (este documento) |
| `<salida>/<nivel>_static.glb` | Geometría estática del nivel (no existe si el nivel no tiene) |
| `<salida>/multimesh/<nivel>_<host>_<geometría>.glb` | Malla de un MultiMesh, en el origen |
| `<salida>/multimesh/<nivel>_<host>_<geometría>_<trozo>.gmmi` | Transformaciones de un trozo del MultiMesh |
| `<assets>/<asset>.asset.json` | Manifiesto de un asset (colección instanciada), mismo formato que un nivel (§4c) |
| `<assets>/<asset>.glb` | Geometría estática del asset (no existe si no tiene) |
| `<assets>/<asset>.tscn` | Escena del asset: la genera Godot a partir de los dos anteriores (§4c) |

Carpetas por defecto, relativas al `.blend` (`levels/<área>/<sala>/source/<sala>.blend`): salida `//../generated/`, assets `//../../_kit/bridge_assets/`. Ambas deben estar dentro del proyecto Godot. El nombre del nivel es el del `.blend` (o `level_name` de la escena), limpiado con `bpy.path.clean_name`.

Escritura: JSON UTF-8 con 2 espacios y `\n` final, floats redondeados a 5 decimales, sin datos que cambien entre exports idénticos. Manifiesto y GLB **solo se reescriben si su contenido cambia**.

## 2. Qué se exporta

- Solo lo que está dentro de la **colección raíz** de la escena, recursivamente.
- Fuera: colecciones excluidas del view layer o con "No exportar" (con todo su contenido) y objetos con "No exportar". La visibilidad del viewport no influye.
- **Rol** de cada objeto: el suyo si no es "Automático"; si no, el de la colección más cercana con rol; si no, por tipo (mallas → geometría estática; el resto no se exporta).
- Orden: recorrido del árbol de colecciones; dentro de cada colección, objetos por nombre.
- Errores de validación bloquean el export (lista en el panel Validación).

## 3. Manifiesto

```json
{
  "format": "gbridge-level",
  "version": 1,
  "level": "catedral_01",
  "static_scene": "catedral_01_static.glb",
  "assets": {},
  "entities": [ … ]
}
```

| Campo | Tipo | Significado |
|---|---|---|
| `format` | string | Siempre `"gbridge-level"`; el lector rechaza otro valor. |
| `version` | int | El lector rechaza versiones mayores que la suya. Solo sube con cambios incompatibles. |
| `level` | string | Nombre del nivel. |
| `static_scene` | string \| null | GLB estático, relativo al manifiesto. |
| `assets` | objeto | Assets que usan las entidades `instance` (§4c): `{nombre: {"manifest": "res://…/<nombre>.asset.json", "hash": "<sha1>"}}`. |
| `entities` | array | Sección 4. |

## 4. Entidades

Comunes:

| Campo | Tipo | Significado |
|---|---|---|
| `uid` | string | Identificador estable: se guarda en el objeto de Blender (`gbridge.uid`) y como metadata `gbridge_uid` del nodo en Godot. Al duplicar un objeto, la copia recibe uno nuevo. |
| `type` | string | `placeholder`, `marker`, `collider`, `area`, `path`, `multimesh`, `instance`, `light` |
| `name` | string | Nombre del objeto; nombre del nodo en Godot. |
| `xform` | 12 floats | Transform en espacio Godot: columnas de la base x, y, z y origen. |

| `type` | Campos propios | Nodo en Godot |
|---|---|---|
| `placeholder` | `scene` (res://…tscn), `props`. `xform` incluye la escala del objeto. | Instancia de `scene`; `props` → propiedades. |
| `marker` | `props`. `xform` sin escala. | `Marker3D`; `props` → metadata. |
| `collider` | forma (§5), `layer`, `mask`, `body` (`static` \| `animatable`), `group`. `xform` = centro de la forma, sin escala. | `StaticBody3D`/`AnimatableBody3D` + `CollisionShape3D` "Shape". Los colisores con el mismo `group` comparten un cuerpo (llamado como el grupo, en el origen) con una `CollisionShape3D` por colisor (llamada como el colisor). |
| `area` | forma (§5), `layer`, `mask`, `scene` (opcional, raíz Area3D), `props`. | `Area3D` (o la instancia de `scene`) + `CollisionShape3D` "Shape". Sin `scene`, `props` → metadata. |
| `path` | `splines`, `props`. `xform` sin escala (horneada en los puntos). | `Path3D` con el primer spline; `props` → metadata. |
| `instance` | `asset` (clave de `assets`), `props`. `xform` incluye la escala. | Instancia de `<asset>.tscn`; `props` → metadata de su raíz. |
| `light` | `light` (`omni` \| `spot`), `color` [r,g,b], `energy`, `range`, `shadow` (bool), `spot_angle` (grados, solo spot), `props`. `xform` sin escala. | `OmniLight3D` / `SpotLight3D`; `props` → propiedades del nodo (p. ej. `omni_attenuation`), aplicadas después de los campos. |
| `multimesh` | `mesh` (GLB relativo al manifiesto), `chunks`: `[{"transforms": "multimesh/….gmmi", "count": n}]`. Sin `xform` ni `props`; `uid` = `<uid del host>:<geometría>`. | `Node3D` con un `BridgeMultiMesh` (`MultiMeshInstance3D`) por trozo, que lee su `.gmmi` y la malla del GLB al cargar (no se guardan en la escena). |

`layer`/`mask`: números de capa de Godot (1–32). Si el objeto no marca ninguno: colisor → capa `world` (o 1), máscara vacía; área → máscara `player` (si existen esos nombres en `project.godot`).

`splines`: `[{"closed": bool, "points": [{"p": [x,y,z], "in": [...], "out": [...], "tilt": float}]}]`, con `in`/`out` relativos al punto (manijas Bézier; ceros en poly/NURBS).

## 4b. Instancias de Geometry Nodes

Opción "Instancias" de cada objeto de geometría estática (o por defecto de la escena):

| Opción | Resultado |
|---|---|
| **MultiMesh** (por defecto) | Solo las instancias, agrupadas por la geometría instanciada (instancias de objeto o de geometría), como entidades `multimesh`. **La geometría propia del objeto no se exporta** (p. ej. el camino guía de un scatter). |
| Realizar | Cada instancia como un nodo de la geometría estática que comparte la malla, junto con la geometría propia del objeto. |
| Ignorar | No se exportan las instancias; la geometría propia del objeto sí. |
| Escenas | Las instancias de una **colección-asset** (§4c) → una entidad `instance` por cada instancia de la colección (`uid` = `<uid del host>:<asset>:<n>`, `name` = `<host>_<asset>_<n>`). Las demás instancias del objeto (de objetos sueltos) se exportan como MultiMesh. |

Trozos: las instancias se agrupan por `floor(x / tamaño)` (`Trozo MultiMesh`, 20 m por defecto) para que Godot descarte los que no ve.

`.gmmi`: `"GMMI0001"`, `uint32` cantidad y por instancia 12 `float32` little-endian (columnas x, y, z de la base y origen, ya en espacio Godot). En un juego exportado, los `.gmmi` deben incluirse en los filtros de recursos no importados de la exportación (`*.gmmi`).

## 4c. Assets (colecciones instanciadas)

- Un **asset** es una colección del mismo `.blend` instanciada por un empty (Objeto → Instancing → Collection; rol automático `instance`) o por Geometry Nodes con la opción "Escenas" (nodo Collection Info). Nombre = nombre de la colección limpiado con `bpy.path.clean_name`; dos colecciones con el mismo nombre limpio son un error.
- La colección-asset suele vivir **fuera de la colección raíz** o excluida del view layer (si no, su contenido se exportaría también como parte del nivel: aviso). Su contenido se exporta aunque esté excluida.
- Contenido: los objetos de la colección y sus subcolecciones con las mismas reglas de rol que un nivel (§2; "No exportar" se respeta, la exclusión del view layer no). Las **luces** de un asset se exportan por defecto (rol `light`); en un nivel solo si se les pone ese rol. Las instancias de Geometry Nodes dentro de un asset siempre se realizan en su GLB. Un empty-instancia dentro de un asset da una entidad `instance` (assets anidados).
- Espacio: el origen del asset es el `instance_offset` de la colección (Propiedades de la colección → Instancing → X/Y/Z); todo `xform` y el GLB del asset son relativos a él.
- `<asset>.asset.json` es un manifiesto `gbridge-level` completo (`level` = nombre del asset, `static_scene` = `<asset>.glb` o null, `assets`, `entities`) y siempre se escribe. Los uid de las entidades del asset son propios del asset.
- `hash`: SHA-1 del `.asset.json` y del `.glb`; cambia si cambia cualquier cosa del asset o de sus assets anidados. Se escriben solo si cambian.
- Los assets de la carpeta compartida no se borran al exportar un nivel (otras salas pueden usarlos).
- **Godot:** al reconstruir, el builder genera `<asset>.tscn` junto al manifiesto del asset (raíz `Node3D` con el nombre del asset, metadata `gbridge_asset_hash`) con el mismo código que un nivel, y lo regenera solo si el hash cambió. Cada entidad `instance` es una instancia de esa escena: editar el asset en Blender y reexportar actualiza todas sus copias. No editar los `.tscn` de assets a mano (se sobrescriben); para lógica, usar placeholders con escenas propias dentro del asset.
- Duplicados enlazados (objetos que comparten malla) **no** se convierten en assets: siguen en la geometría estática compartiendo la malla (§8). Para que algo sea un asset, se instancia su colección.

### Luces

| Campo | Desde Blender |
|---|---|
| `light` | Punto → `omni`; foco → `spot`. Sol y área no se exportan (aviso). |
| `color` | Color de la luz. |
| `energy` | Potencia en W / 100 (100 W → 1,0). |
| `range` | "Custom Distance" de la luz si está activo; si no, 10 m. |
| `shadow` | "Shadow" de la luz. |
| `spot_angle` | Mitad del tamaño del foco, en grados. |

Las luces de glTF no sirven (llegan con alcance infinito y energías de miles): por eso son entidades. Ajustes finos con props, p. ej. `light_energy`, `omni_range`, `omni_attenuation`, `light_volumetric_fog_energy`.

## 5. Formas

Medidas en metros, con la escala del objeto horneada; derivadas de la caja local del objeto (la de la malla evaluada, o ±`empty_display_size` en un empty).

| `shape` | Campos | Cálculo |
|---|---|---|
| `box` | `size` [x,y,z] | Dimensiones de la caja (ejes de Godot). |
| `sphere` | `radius` | Mitad de la mayor dimensión. |
| `capsule` | `radius`, `height` | Radio: mitad del mayor entre X e Y de Blender; altura: Z de Blender (mínimo 2·radio). Eje vertical. |
| `cylinder` | `radius`, `height` | Igual que la cápsula, sin mínimo. |
| `convex` | `points` | Vértices de la malla evaluada, relativos al centro. |
| `trimesh` | `faces` | Triángulos de la malla evaluada (3 puntos por triángulo). |

## 6. Coordenadas

- Blender `(x, y, z)` → Godot `(x, z, -y)`; matrices como `C · M · C⁻¹`. Todo transform del manifiesto ya está en espacio Godot.
- 1 unidad de Blender = 1 m = 1 unidad de Godot.
- El plano de juego es XY en Godot (XZ en Blender); la profundidad es `z` de Godot (`-y` de Blender).

## 7. Props

Las props de una entidad son las **propiedades personalizadas** del objeto en Blender (las que empiezan por `_` y las de Blender/addons se ignoran).

| Blender | JSON | Godot |
|---|---|---|
| bool, int, float, string | igual | Convertido al tipo de la propiedad destino (`int`, `float`, `StringName`, `NodePath`…). |
| array de números | array | `Vector2/3/4` o `Color` según la propiedad destino. |
| puntero a otro objeto exportado | `{"$ref": "<uid>"}` | `NodePath` relativo al nodo de esa entidad. |
| string `res://…` | string | Se carga si la propiedad destino es un recurso. Forma explícita: `{"$res": "res://…"}`. |

En Godot las props se asignan **antes** de que el nodo entre al árbol: su `_ready()` ya las ve. Una prop que la escena no tiene se guarda como metadata y el builder avisa.

## 8. GLB estático

- Una copia temporal por objeto (malla evaluada, o la base con "No aplicar" modificadores); el `.blend` no se modifica.
- Colores de vértice convertidos a Face Corner + Byte Color y exportados como `COLOR_0` (el atributo activo).
- Modo de colores por objeto (o por defecto de la escena): `DATA` (por defecto) los exporta sin más; `ALBEDO` además marca la malla con el extra glTF `"gbridge_vertex_colors": "albedo"` (las mallas con colores llevan siempre ese extra, `"data"` o `"albedo"`); `NONE` los quita. El modo forma parte de la comparación para compartir malla.
- En Godot, la extensión `LevelBridgeVertexColors` del plugin, al importar los GLB del puente (`*_static.glb`, `bridge_assets/`), activa `vertex_color_use_as_albedo` solo en las superficies de mallas `albedo`: si el material lo comparten otras mallas, les da una copia `<material>_vertex_color`; si no tienen material, les crea uno.
- Nodos con el nombre del objeto original; transforms de mundo (sin jerarquía).
- Objetos cuya geometría final es idéntica comparten una sola malla en el GLB y en Godot todos sus `MeshInstance3D` usan el mismo `Mesh`: duplicados enlazados (Alt+D), también con modificadores si dan el mismo resultado, y objetos independientes con la misma geometría (se compara un hash de vértices, caras, atributos, UV, colores y materiales de la malla evaluada). Las mallas se llaman `<nivel>_<malla>` en Godot. Para instancias de escena (assets con colisiones, luces, scripts) están las colecciones instanciadas *(fase 4)*; para repetir cientos de copias, Geometry Nodes → MultiMesh *(fase 3)*.
- En Godot, Rebuild reimporta antes el GLB si cambió desde su última importación (si no, el editor podría construir con la versión anterior en caché). `Generated/` se guarda dentro de la escena de la sala: tras Rebuild hay que guardarla. El builder guarda el MD5 del manifiesto (`built_manifest_hash`) y avisa al cargar si el manifiesto cambió desde entonces.
