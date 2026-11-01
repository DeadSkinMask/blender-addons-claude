# Godot Level Bridge — Análisis y plan por fases

Puente Blender 5.2 ⇄ Godot 4.7 para un metroidvania 2.5D (jugabilidad 2D, todo 3D), usando Blender como editor de niveles completos.

**Decisiones tomadas**
- Física **3D con el eje de profundidad bloqueado** (`CharacterBody3D` + axis lock). Los colisores son formas 3D.
- **Un nivel por `.blend`.**
- Proyecto Godot nuevo, desde cero. El borrador (`/home/luis/GIT/personal/Godot/gothboobsvania-master`, Godot 4.6) solo se usa como referencia.
- Todo vive dentro del `.blend` del nivel; no se usan bibliotecas de assets enlazadas.
- Los mapas son sobre todo Geometry Nodes + enemigos y entidades con empties → las instancias de GN tienen prioridad sobre las instancias de colección.
- Placeholders en Blender: caja de color con el volumen de la entidad si se conoce; si no, un marcador (4.6).
- Capas de física: propuesta en 7.4.
- Tests del plugin de Godot con `/home/luis/GIT/personal/Godot4.7/Godot_v4.7.2-stable_linux.x86_64`.

---

## 1. El problema

Exportar un nivel completo como un único GLB deja a Godot con un montón de mallas y pierde todo lo que hace que un nivel sea un nivel:

| Lo que se necesita | Lo que da un GLB plano | Lo que propone el puente |
|---|---|---|
| Colisiones baratas y precisas | `-col`/`-colonly` → trimesh o convex, escala no uniforme | `CollisionShape3D` con `BoxShape3D`, `SphereShape3D`, `CapsuleShape3D`, `CylinderShape3D` (convex/trimesh solo si se piden) |
| Triggers, zonas de cámara, transiciones | No existen | `Area3D` con forma primitiva, capas y propiedades |
| Enemigos, pickups, spawns, puertas | Mallas sueltas o nada | Placeholders que instancian `.tscn` reales y les asignan propiedades `@export` |
| Reutilizar assets (pilares, antorchas…) | Cada copia es una malla independiente | Cada asset se exporta **una vez**; el nivel guarda instancias de su escena |
| Scatter de Geometry Nodes | O se realiza todo a malla, o se pierde | `MultiMeshInstance3D` (formato GMMI que ya existe) o instancias de escena |
| Plataformas móviles, raíles de cámara | Las curvas se pierden | `Path3D` con su `Curve3D` |
| Vertex colors | Se pierden en silencio según el dominio, el tipo o el material | Normalizados al exportar, sin tocar el `.blend` |
| Iterar desde Godot | No hay vuelta | Godot exporta un catálogo de escenas y los cambios; Blender los importa como placeholders |

## 2. Decisión de arquitectura

### 2.1 Opciones evaluadas

| Opción | A favor | En contra |
|---|---|---|
| A. Un GLB con `extras` + script post-import de Godot | Un solo archivo; reimport automático | glTF no puede referenciar escenas externas (las instancias se vuelven copias); los `extras` limitan los tipos; el resultado importado no se puede editar ni sirve para ida y vuelta; un nivel grande reimporta todo por cualquier cambio |
| **B. Manifiesto JSON + GLBs separados + constructor en Godot** ✅ | Cada pieza en su formato natural; assets compartidos entre niveles; instancias reales de escenas; propiedades con tipos ricos; diff legible en git; base para la vuelta Godot→Blender | Dos plugins (Blender y Godot) que deben mantener el mismo contrato |
| C. Escribir `.tscn` directamente desde Blender | Godot lo abre sin plugin | Hay que generar a mano los UIDs y `ext_resource` de Godot, frágil entre versiones; sin ida y vuelta |

**Recomendación: B.** Es la misma filosofía que ya funcionó con el scatter (GLB + binario + loader `@tool`), generalizada a todo el nivel.

### 2.2 Visión general

```
 Blender (.blend = nivel)                          Godot (res://)
 ───────────────────────                           ─────────────────────────────
 Colección raíz del nivel                ─export─▶ levels/<nivel>/
   │ 1. resolver qué se exporta                      <nivel>.level.json   ← manifiesto
   │ 2. validar (escala, colores, uids…)             <nivel>_static.glb   ← malla no instanciada
   │ 3. staging: copias temporales evaluadas         multimesh/*.gmmi + *.glb
   │ 4. escribir GLBs + JSON + GMMI                <carpeta de assets>/<asset>.glb (+ .asset.json)
   │                                                       │
   │                                                       ▼
   │                                          LevelBuilder (@tool) reconstruye
   │                                          un sub-árbol "Generated" en el .tscn
   │                                                       │
 Placeholders con preview + props    ◀──import── catalog.json + previews/*.glb
 Cambios de transform/props (por uid) ◀──import── <nivel>.godot_edits.json
```

### 2.3 Por qué un constructor `@tool` y no un `EditorImportPlugin`

Un import plugin produce un recurso importado de solo lectura: los cambios hechos en Godot se pierden o viven en una escena "wrapper", lo que complica la vuelta a Blender. Un nodo `LevelBuilder` con botón **Rebuild** (como `GodotScatterBinaryLoader`) genera nodos reales dentro de la escena del nivel, puede detectar si se editaron a mano antes de sobrescribirlos y puede exportar esos cambios. El precio es que el rebuild es explícito; más adelante se puede añadir un aviso cuando el manifiesto cambió.

Los GLB (estático, assets, mallas de multimesh) sí pasan por el import normal de Godot: ahí no se pierde nada.

### 2.4 Staging: el exportador nunca modifica el `.blend`

Para cumplir varios requisitos a la vez (aplicar o no modificadores, separar las instancias de Geometry Nodes de la malla, convertir vertex colors), el export no le pasa los objetos originales al exportador glTF. Para cada objeto de geometría estática crea una **copia temporal** en una escena auxiliar:

1. Malla evaluada con `obj.evaluated_get(depsgraph).to_mesh()`, o malla base si el objeto tiene "modificadores: no aplicar". La malla evaluada contiene solo el componente de malla: las instancias de Geometry Nodes no vienen incluidas y se recogen aparte desde `depsgraph.object_instances`.
2. Conversión de vertex colors en la copia (sección 4.4).
3. Exportación glTF de esa escena auxiliar con parámetros fijos.
4. Borrado de las copias y restauración de la selección y del objeto activo.

Así el `.blend` del usuario queda intacto y el resultado no depende de opciones que se hayan dejado puestas en el diálogo de glTF.

## 3. Qué se exporta (alcance)

### 3.1 Métodos posibles

| Método | Ventajas | Problemas |
|---|---|---|
| Solo selección | Rápido para probar | Fácil olvidar algo; un export parcial pisaría el manifiesto completo y borraría entidades en Godot |
| Flag por objeto | Control fino | Hay que marcar cientos de objetos; lo nuevo no se exporta hasta que se marca |
| Visibilidad (ojo / cámara) | Ya existe en Blender | El ojo se usa para trabajar cómodo; ocultar algo para editar no debería sacarlo del nivel |
| **Colección raíz del nivel** ✅ | Visible en el outliner, determinista, todo lo nuevo dentro entra solo, los objetos auxiliares quedan fuera | Hay que respetar la estructura de colecciones |

### 3.2 Regla propuesta

1. **Colección raíz:** en la escena se elige la colección del nivel (`scene.gbridge.root`). Solo se exporta lo que está dentro, de forma recursiva. Lo que está fuera (referencias, cámaras de trabajo, blockouts viejos) no se exporta nunca.
2. **Exclusiones explícitas:**
   - Sub-colecciones excluidas del View Layer (casilla del outliner desmarcada) o con `gbridge.skip` → fuera, con todo su contenido.
   - Objetos con `gbridge.skip` → fuera.
   - El ojo del viewport **no** afecta al export (se puede ocultar algo para editar sin perderlo).
3. **Rol por colección:** una sub-colección puede tener un tipo de entidad por defecto. Ejemplo: todas las mallas de `Colisiones/` son `collider` con forma ajustada a su caja; todas las curvas de `Paths/` son `path`. Un objeto puede sobrescribir el rol de su colección.
4. **Objetos sin rol:** las mallas van a la geometría estática, los empties sin rol se ignoran, las curvas sin rol se ignoran. Los colisores, áreas y markers nunca van a la geometría visual.
5. **"Exportar selección" no existe como export del nivel.** Para iterar rápido hay dos acciones aparte que no tocan el manifiesto: *Validar selección* y *Exportar asset seleccionado* (re-exporta solo la colección-asset del objeto activo).
6. Botón **"Mostrar qué se exporta"**: selecciona en el viewport todo lo que entraría, y lista los conteos por tipo. Sirve para comprobar el alcance antes de exportar.

Las marcas se guardan en una `PropertyGroup` (`obj.gbridge`, `collection.gbridge`, `scene.gbridge`) con interfaz propia, no en custom properties sueltas: así tienen tipos y valores fijos. Las **custom properties normales** del objeto (`obj["speed"] = 3`) siguen sirviendo como props de la entidad.

## 4. Reglas de export

### 4.1 Convenciones

- **Ejes:** Blender `(x, y, z)` → Godot `(x, z, -y)`; matrices como `C · M · C⁻¹` (igual que el scatter). El manifiesto guarda transforms **ya en espacio Godot**; Blender convierte al importar.
- **Plano de juego:** el plano XY de Godot (profundidad en z de Godot) es el plano XZ de Blender; la profundidad es **y de Blender** (`z_godot = -y_blender`). El addon tiene una preferencia "plano de juego en y = 0" que usa el validador.
- **Unidades:** 1 unidad Blender = 1 m = 1 unidad Godot.
- **Identidad estable (`uid`):** cada entidad tiene un `uid` (string corto) guardado en el objeto de Blender y como metadata `gbridge_uid` en Godot. Es la clave de la ida y vuelta y de las referencias entre entidades. Gotcha: duplicar en Blender copia la `PropertyGroup` y, con ella, el uid → el validador detecta uids duplicados y regenera el de la copia (al asignar el uid se guarda también el nombre del objeto, para saber cuál es el original).
- **Rutas:** el addon conoce la raíz del proyecto Godot (preferencia del addon) y traduce rutas de disco ↔ `res://`. La carpeta de export debe estar dentro del proyecto.

### 4.2 Modificadores

Política por objeto (con valor por defecto en la escena):

| Política | Malla exportada | Instancias de Geometry Nodes |
|---|---|---|
| **Aplicar** (por defecto) | Malla evaluada (lo que se ve en el viewport) | Según la política de instancias |
| No aplicar | Malla base, sin modificadores | Ninguna |

Política de **instancias** por objeto que tenga instancias evaluadas (normalmente un modificador de Geometry Nodes generador):

| Política | Resultado |
|---|---|
| **MultiMesh** (por defecto) | Agrupadas por objeto/malla instanciada → `MultiMeshInstance3D` (solo visual, sin colisión) |
| Escenas | Si lo instanciado es una colección-asset, cada instancia → instancia de su escena (sirve cuando el asset tiene luces, colisiones o scripts) |
| Realizar | Se hornean en la malla estática (para pocas instancias o geometría única) |
| Ignorar | No se exportan |

Un objeto puede generar malla e instancias a la vez (por ejemplo, un muro con antorchas instanciadas): la malla va al GLB estático y las instancias siguen su política. Se usa el depsgraph de viewport, así que un modificador desactivado en el viewport no se exporta.

### 4.3 Escala

El validador clasifica cada objeto y ofrece un botón "arreglar" cuando es seguro hacerlo:

| Caso | Geometría estática | Colisor / área | Placeholder / instancia de escena |
|---|---|---|---|
| Escala ≠ 1 uniforme | Info (válida en glTF) | Se hornea en el tamaño de la forma | Aviso: escala un `.tscn` con física → comportamiento raro |
| Escala no uniforme | Aviso | Se hornea; **aviso si además hay rotación** que impida representarla con la forma | **Error** |
| Escala negativa | Aviso (normales invertidas) | Error | Error |
| Escala aplicada en el padre (jerarquía) | Igual, usando la matriz mundial | Igual | Igual |

"Arreglar" = aplicar la escala con `transform_apply`, solo si la malla no la comparten otros objetos (en ese caso propone convertirlo en asset). Godot se lleva mal con escala no uniforme en formas de física, por eso los colisores salen siempre con escala 1 y el tamaño horneado en `size`, `radius` o `height`.

### 4.4 Vertex colors

Comprobado en Blender 5.2.3: el exportador glTF trae por defecto `export_vertex_color='MATERIAL'`, que **solo exporta los colores que usa algún material**. Si el material no lee el atributo, el color se descarta sin avisar. Por eso:

- En el staging se convierte el atributo de color a **Face Corner + Byte Color** (`geometry.color_attribute_convert(domain='CORNER', data_type='BYTE_COLOR')`) sobre la copia, no sobre el original.
- Se exporta con `export_vertex_color='ACTIVE'` (o `'NAME'` con un nombre fijo, por ejemplo `Col`), sin depender del material.
- El validador avisa si una malla tiene varios atributos de color y no está claro cuál es el activo, o si tiene colores en dominio Point / Float.
- Pendiente de verificar en la fase 1 con Godot 4.7.2: que el material importado tenga `vertex_color_use_as_albedo`, o si el builder debe activarlo (o asignar un material propio que lea `COLOR`).

### 4.5 Curvas → `Path3D`

- Objetos `Curve` de Blender con rol `path` → `Path3D` con `Curve3D`.
- Bézier: posición del punto, y `in`/`out` **relativos** (Blender guarda las manijas en coordenadas absolutas → se restan del punto), con `tilt` incluido. Poly: puntos sin manijas. NURBS: se muestrean a puntos.
- Una curva con varios splines → un `Path3D` hijo por spline.
- Curvas cíclicas: usar `Curve3D.closed` si existe en 4.7 (verificar); si no, repetir el primer punto.
- Los modificadores y la geometría de la curva (bevel, extrude) no se exportan como path; si una curva también debe verse, se marca además como geometría estática y se exporta su malla evaluada.
- Props como cualquier entidad; un placeholder puede referenciar un path (`{"$ref": uid}`) para plataformas móviles o raíles de cámara.

### 4.6 Placeholders y markers en el viewport

- El placeholder es un empty. Como su escala debe ser 1 (4.3), el volumen no se toma de la escala del empty: se guarda como propiedad (`gbridge.volume`: tamaño + offset, en metros).
- El addon dibuja en el viewport una **caja de color** con ese volumen (contorno + relleno semitransparente) y el nombre de la escena encima, con un draw handler de `gpu`/`blf`. Es solo visual: no se renderiza ni se exporta.
- El color sale de la categoría (subcarpeta de `res://entities/`: enemigos rojo, pickups amarillo, triggers verde…), configurable en las preferencias.
- Sin volumen, el empty se ve como marcador normal (flechas o ejes) con el mismo color en la etiqueta.
- Hasta la fase 5 el volumen se escribe a mano o se copia de otro placeholder con la misma escena (botón "copiar volumen a los que usan esta escena"); desde la fase 5 viene del AABB que calcula Godot.
- El mismo overlay dibuja las formas de los colisores y las áreas de la fase 2 (caja, esfera, cápsula, cilindro).

## 5. Contrato (formato v1, borrador)

### 5.1 Manifiesto `<nivel>.level.json`

```jsonc
{
  "format": "gbridge-level", "version": 1,
  "level": "castle_entrance",
  "static_scene": "castle_entrance_static.glb",
  "assets": {
    "pillar_a": { "glb": "res://levels/catedral/_kit/bridge_assets/pillar_a.glb", "hash": "9f2c…", "has_entities": true }
  },
  "entities": [
    { "uid": "k3f9a", "type": "collider", "name": "Floor_01",
      "body": "static", "shape": "box", "size": [12.0, 1.0, 4.0],
      "layer": ["world"], "mask": [], "group": "room_a",
      "xform": [1,0,0, 0,1,0, 0,0,1, 6.0,-0.5,0.0] },

    { "uid": "p02xx", "type": "area", "name": "Transition_East",
      "shape": "box", "size": [1, 4, 4], "scene": "res://triggers/room_transition.tscn",
      "props": { "target_level": "res://levels/hall.tscn", "target_spawn": { "$ref": "q8w1e" } },
      "xform": [...] },

    { "uid": "e77ab", "type": "placeholder", "name": "Bat_03",
      "scene": "res://enemies/bat.tscn",
      "props": { "patrol_radius": 3.5, "aggressive": true, "drop": { "$res": "res://items/heart.tres" } },
      "xform": [...] },

    { "uid": "q8w1e", "type": "marker", "name": "Spawn_West", "props": { "facing": "right" }, "xform": [...] },

    { "uid": "c71d0", "type": "path", "name": "Platform_Rail",
      "splines": [ { "closed": false,
                     "points": [ { "p": [0,0,0], "in": [-1,0,0], "out": [1,0,0], "tilt": 0.0 } ] } ],
      "xform": [...] },

    { "uid": "i0001", "type": "instance", "asset": "pillar_a", "xform": [...] },

    { "uid": "g5512", "type": "multimesh", "name": "Grass_Scatter",
      "chunks": [ { "mesh": "multimesh/grass_mesh.glb", "transforms": "multimesh/grass_c0_0.gmmi" } ] }
  ]
}
```

- `xform` = 12 floats: columnas de la base x, y, z y origen (mismo orden que GMMI).
- Tipos de propiedades: JSON nativo (`bool`, `int`, `float`, `string`); arrays de 2/3/4 números → `Vector2/3/4` o `Color` según el schema; `{"$ref": uid}` → `NodePath` a otra entidad; `{"$res": path}` → recurso cargado.
- Sin schema, el constructor convierte según el tipo de la propiedad destino (`typeof(node.get(name))`).

### 5.2 Tipos de entidad

| Tipo | Origen en Blender | Resultado en Godot |
|---|---|---|
| `static` (implícito) | Mallas sin rol | Dentro de `<nivel>_static.glb` |
| `collider` | Malla o empty con rol; la forma se deriva de las dimensiones locales | `StaticBody3D` / `AnimatableBody3D` + `CollisionShape3D`. Los colisores con el mismo `group` comparten cuerpo |
| `area` | Igual que collider | `Area3D` (o la raíz de `scene` si se indica una escena basada en Area3D) + forma |
| `placeholder` | Empty con `scene = res://…tscn` | Instancia de la escena + `props` aplicadas a sus `@export` |
| `marker` | Empty | `Marker3D` con metadata (spawns, puntos de cámara, waypoints) |
| `path` | Curva | `Path3D` + `Curve3D` |
| `instance` | Empty que instancia una colección del mismo `.blend` | Instancia de la escena del asset |
| `multimesh` | Instancias evaluadas con política MultiMesh | `MultiMeshInstance3D` por chunk |

Formas: `box`, `sphere`, `capsule`, `cylinder`, y bajo demanda `convex` (hull de la malla) y `trimesh` (malla tal cual, para los casos que lo justifiquen).

### 5.3 Distribución de archivos

El puente no impone carpetas: cada `.blend` define dos rutas de salida (`scene.gbridge`), con valores por defecto relativos al propio `.blend`. Con la estructura de salas del juego (`/home/luis/GIT/personal/Godot/elegia/docs/arquitectura.md` §3):

```
res://levels/catedral/                    ← área
  _kit/bridge_assets/                     ← "carpeta de assets": compartida por las salas del área
    pillar_a.glb
    pillar_a.asset.json                   ← solo si la colección tiene colisores/entidades
    pillar_a.tscn                         ← generado por Godot a partir de los dos anteriores
  catedral_01/                            ← sala
    source/catedral_01.blend              ← (.gdignore) Godot no importa el .blend
    generated/                            ← "carpeta de salida": solo la escribe el puente
      catedral_01.level.json
      catedral_01_static.glb
      multimesh/…
    catedral_01.tscn                      ← escena de la sala con LevelBuilder (se crea una vez)
```

Por defecto, la salida es `../generated/` y los assets `../../_kit/bridge_assets/` respecto al `.blend`.

Los assets se reexportan solo si cambia su hash (geometría + materiales + entidades), para no provocar reimports masivos en Godot al exportar un nivel grande.

## 6. Qué es genérico y qué depende del juego

El puente no conoce el juego: no sabe qué es un enemigo, una puerta ni un save point. Todo lo específico del proyecto le llega como **datos**, no como código:

| Necesita del proyecto Godot | De dónde lo saca | Si el juego cambia… |
|---|---|---|
| Ruta raíz del proyecto | Preferencia del addon | Se cambia una ruta |
| Nombres de las capas de física | `[layer_names]` del `project.godot` | Se vuelve a leer el archivo |
| Escenas a instanciar y sus props | Texto `res://…` en el placeholder; desde la fase 5, el catálogo generado por Godot | Se regenera el catálogo |
| Carpeta de salida de los niveles | Preferencia del addon (por defecto `res://levels/`) | Se cambia una ruta |

Lo que sí depende de cómo esté hecho el juego son unas pocas **convenciones** (sección 7): cómo recibe una escena sus props, dónde vive el `LevelBuilder` y qué capas existen. Son decisiones de arquitectura del juego que conviene tomar de todas formas, y son pequeñas.

Lo único realmente específico de un metroidvania (rooms, minimapa, transiciones entre salas) se saca del puente y pasa a ser **código del juego** que consume entidades genéricas (`area`, `marker`) exportadas por el puente (ver fase 7).

### ¿Rediseñar primero el juego y después el addon?

No recomiendo esperar a tener el juego rediseñado:
- El addon es la herramienta con la que vas a construir los niveles del juego nuevo. Sin él, los primeros niveles vuelven a montarse a mano o con el GLB plano, que es justo lo que se quiere dejar atrás.
- El contrato del puente es genérico; lo que se decida del juego después (nuevos enemigos, nuevas mecánicas) entra como escenas y props, sin tocar el addon.
- El plugin de Godot necesita un proyecto donde probarse, y ese proyecto puede ser el esqueleto del juego nuevo.

Sí recomiendo un orden: **primero un esqueleto mínimo del juego nuevo con las convenciones de la sección 7** (es poco trabajo y no depende del addon), y luego las fases del puente sobre ese proyecto. El juego y el puente crecen juntos: cada fase del puente se prueba con un nivel real del juego.

## 7. Convenciones mínimas del proyecto Godot (antes de la fase 0)

### 7.1 Estructura de carpetas

Definida en el proyecto del juego: `/home/luis/GIT/personal/Godot/elegia/docs/arquitectura.md` §2–3. Lo que le importa al puente:

- `res://addons/level_bridge/`: el plugin.
- `res://entities/<categoría>/<nombre>/`: escenas que se colocan desde Blender; es la carpeta que recorre el catálogo en la fase 5.
- `res://levels/<área>/<sala>/`: una sala por `.blend`, con `source/` (el `.blend`) y `generated/` (salida del puente), según 5.3.

### 7.2 Contrato de una escena-entidad

- La raíz de la escena es el nodo que recibe las props, y las props son variables `@export` de su script. El builder solo hace `node.set(nombre, valor)`; no llama a métodos del juego.
- Las props se asignan **antes** de añadir el nodo al árbol, así que `_ready()` ya las ve con su valor final.
- Referencias a otras entidades: `@export var target: NodePath` (el builder lo resuelve desde el uid).
- Una entidad no debe depender de su escala (la escala se valida como error, ver 4.3); si su tamaño es configurable, se expone como prop (`@export var size: Vector3`).

### 7.3 Escena de un nivel

```
<sala>.tscn
  Room (Node3D, script del juego)
    LevelBuilder        ← plugin: lee el manifiesto
      Generated/        ← lo crea y lo reemplaza el builder; no editar a mano
    Authored/           ← lo que se añade a mano en Godot (fase 6)
```

### 7.4 Capas de física propuestas

En Godot cada objeto tiene `collision_layer` (lo que es) y `collision_mask` (lo que detecta). Propuesta para el proyecto nuevo:

| # | Nombre | Quién está en esta capa |
|---|---|---|
| 1 | `world` | Suelo, muros y techos sólidos (StaticBody) |
| 2 | `one_way` | Plataformas atravesables desde abajo |
| 3 | `player` | Cuerpo del jugador (CharacterBody3D) |
| 4 | `enemy` | Cuerpos de los enemigos |
| 5 | `player_hurtbox` | Área donde el jugador recibe daño |
| 6 | `enemy_hurtbox` | Área donde un enemigo recibe daño |
| 7 | `player_hitbox` | Ataques del jugador (armas, hechizos, proyectiles) |
| 8 | `enemy_hitbox` | Ataques de enemigos y peligros del escenario (pinchos, lava) |
| 9 | `breakable` | Hurtbox de muros/objetos destructibles |
| 10 | `pickup` | Ítems, corazones, dinero |
| 11 | `interactable` | Puertas, palancas, save points, NPCs |
| 12 | `trigger` | Transiciones de sala, eventos, zonas de cámara, agua |
| 13–32 | libres | Reservadas para lo que aparezca |

Máscaras típicas (lo que detecta cada uno):

| Objeto | Capa | Máscara |
|---|---|---|
| Cuerpo del jugador | `player` | `world`, `one_way` |
| Cuerpo de un enemigo | `enemy` | `world`, `one_way` |
| Hitbox del jugador | `player_hitbox` | `enemy_hurtbox`, `breakable` |
| Hitbox de enemigo / peligro | `enemy_hitbox` | `player_hurtbox` |
| Hurtboxes | su capa | ninguna (las detecta la hitbox) |
| Pickup | `pickup` | `player` |
| Trigger | `trigger` | `player` |
| Área de interacción del jugador | ninguna | `interactable` |

Notas:
- Jugador y enemigos no chocan físicamente (como en Aria of Sorrow, se atraviesan y el daño por contacto lo da la hitbox del enemigo). Si alguno debe empujar, se añade `enemy` a la máscara del jugador.
- `one_way`: como CharacterBody3D no tiene colisión de un solo sentido, el jugador quita el bit 2 de su máscara mientras sube o cuando se pulsa abajo + salto. Es código del jugador; el puente solo pone esas plataformas en la capa `one_way`.
- El puente lee estos nombres del `project.godot`; si cambian, no hay que tocar el addon.

## 8. Fases

Cada fase deja algo usable y amplía el formato de forma compatible (el campo `version` sube solo si hay cambios incompatibles). Orden ajustado a tu uso: los mapas son sobre todo Geometry Nodes, enemigos y entidades con empties, así que las instancias de Geometry Nodes van antes que las instancias de colección.

### Fase previa (juego) — Esqueleto del proyecto nuevo
- Fases G0 y G1 del juego (`/home/luis/GIT/personal/Godot/elegia/docs/plan_de_fases.md`): proyecto Godot 4.7 con su estructura, las capas de 7.4 y un jugador mínimo.
- No forma parte del addon, pero es donde se prueban todas las fases siguientes. El orden conjunto juego + puente está en ese mismo documento.

### Fase 0 — Contrato y esqueleto del puente
- **Blender:** extensión (`blender_manifest.toml`, multiarchivo, misma estructura de tests headless que Sculpt Brush Shelf). Preferencias: raíz del proyecto Godot. Panel en la sidebar "Godot Level". `PropertyGroup` en `Scene`, `Collection` y `Object` (rol, uid, skip, políticas).
- **Godot:** `addons/level_bridge/` con `plugin.cfg` y un nodo `LevelBuilder` vacío (botón Rebuild, ruta al manifiesto).
- **Doc:** `FORMAT.md` con el contrato de la sección 5, que pasa a ser la referencia de ambos lados.
- **Aceptación:** exportar una colección raíz vacía produce un manifiesto válido que `LevelBuilder` lee sin errores.

### Fase 1 — Alcance, staging, validación, geometría estática, placeholders y markers
- **Blender:**
  - Resolución del alcance (sección 3) y botón "Mostrar qué se exporta".
  - Staging (2.4) con la política de modificadores *Aplicar / No aplicar* y la conversión de vertex colors (4.4).
  - Validador con panel de problemas (error / aviso / info), clic para seleccionar el objeto y botón "arreglar" cuando aplique: escala (4.3), uids duplicados, colores, placeholders sin escena. Los errores bloquean el export.
  - Placeholders y markers con su visualización (4.6): empty con `scene` (selector de `.tscn` dentro del proyecto), volumen opcional y lista de props editables (nombre + tipo + valor).
- **Godot:** el builder crea `Generated/`, instancia el GLB estático y los placeholders (con el `owner` correcto para que se guarden en el `.tscn`) y aplica las props con conversión de tipos; resuelve los `$ref` a `NodePath` en una segunda pasada. Verificar vertex colors en el material importado.
- **Aceptación:** nivel con suelo con vertex colors, 3 enemigos con propiedades distintas y un spawn; objetos fuera de la colección raíz no aparecen; el rebuild es idempotente (dos rebuilds seguidos no cambian el `.tscn`).

### Fase 2 — Colisiones primitivas, áreas y paths
- **Blender:** roles `collider`, `area` y `path`; forma elegida en un enum, tamaño ajustado a la caja local (con botón "ajustar a la malla"); visualización de cajas, cápsulas, cilindros y esferas en el viewport con el mismo overlay de 4.6. Capas y máscaras **leyendo los nombres de `[layer_names]` del `project.godot`**, con presets por rol (un `area` de tipo trigger sale con capa `trigger` y máscara `player`). Agrupación por cuerpo. Validador: escalas (4.3), formas degeneradas, colisores que no cruzan el plano de juego. Export de curvas (4.5).
- **Godot:** construcción de cuerpos, áreas, formas y `Path3D`; las áreas basadas en escena (por ejemplo `entities/triggers/room_transition.tscn`) reciben su forma como hijo.
- **Aceptación:** el personaje camina por un nivel cuyas colisiones son solo primitivas, sin ningún `ConcavePolygonShape3D` salvo los pedidos explícitamente; una plataforma móvil sigue un path exportado.

### Fase 3 — Instancias de Geometry Nodes
- **Blender:** políticas de instancias (4.2). Portar la lógica del addon de scatter (copiar, no compartir código) ampliada a: varios objetos fuente por objeto generador (agrupando instancias por su referencia), y **partición en chunks por celda de rejilla** para que el frustum culling funcione en mapas grandes (un único MultiMesh de todo el mapa nunca se descarta). Política "Realizar".
- **Godot:** port de `GodotScatterBinaryLoader` al builder, con el mismo formato GMMI0001.
- **Aceptación:** scatter de hierba en un mapa entero con varios chunks que se ocultan y aparecen según la cámara.

### Fase 4 — Assets e instancias de colección
- **Blender:** instancias de colección dentro del mismo `.blend` (no se usan bibliotecas enlazadas) → cada colección única se exporta una vez a la carpeta de assets (5.3) como `<nombre>.glb`, con el origen en el `instance_offset` de la colección. Si la colección contiene colisores, áreas o placeholders, se escribe también `<nombre>.asset.json` (mismo formato que un nivel). Caché por hash. Acción "Exportar asset seleccionado". Política "Escenas" de 4.2: instancias de Geometry Nodes que apuntan a una colección-asset → instancias de su escena.
- **Godot:** el builder genera `<nombre>.tscn` en la carpeta de assets (GLB + entidades del asset) y el nivel instancia esa escena. Editar el asset y reexportar actualiza todas sus copias.
- **Decidir aquí:** si los *linked duplicates* (objetos que comparten malla) se promueven automáticamente a assets o se quedan en la geometría estática.
- **Aceptación:** 200 pilares en el nivel → 1 GLB de asset y 200 instancias de escena; un muro generado por GN con antorchas instanciadas como escenas (con su luz).

### Fase 5 — Godot → Blender I: catálogo de escenas
- **Godot:** comando de editor "Export catalog" que recorre `res://entities/` y escribe `catalog.json`: por escena, su ruta, su categoría (subcarpeta), su **AABB** (para el volumen del placeholder), sus `@export` (nombre, tipo, hint, enum, valor por defecto) y, opcionalmente, un `previews/<escena>.glb` generado con `GLTFDocument` (solo la parte visual).
- **Blender:** importar el catálogo → selector para colocar placeholders con su volumen y color de categoría ya rellenados (o con la malla de preview, enlazada y no exportada) y **props tipadas con los valores por defecto del schema**, en vez de escribirlas a mano.
- **Aceptación:** colocar un enemigo desde Blender y ver su volumen correcto sin escribir nada a mano; editar sus props con los tipos correctos.

### Fase 6 — Godot → Blender II: ida y vuelta de cambios
- **Modelo de propiedad (ownership):**
  - Las entidades que vienen del manifiesto viven en `Generated/` y las gestiona el builder.
  - Lo que el usuario añade en Godot va en `Authored/` (cualquier `.tscn`, con `gbridge_uid` asignado al exportar).
  - **Adopción:** al importar en Blender, las entidades de `Authored/` se convierten en placeholders de Blender; en el siguiente export pasan al manifiesto y el builder retira la copia de `Authored/` (mismo uid, con confirmación). Así nunca hay dos dueños.
- **Godot:** "Send to Blender" escribe `<nivel>.godot_edits.json` con las entidades nuevas de `Authored/` y las diferencias de transform y props de los nodos de `Generated/` frente al manifiesto. El builder avisa antes de un rebuild si hay cambios sin enviar.
- **Blender:** importa el diff con una vista previa (lista de cambios por uid, aceptar o rechazar) y crea los placeholders nuevos con el volumen del catálogo, dentro de la colección raíz.
- **Aceptación:** mover un enemigo y cambiarle una prop en Godot, enviar, importar en Blender, reexportar y hacer rebuild → el cambio persiste y no hay duplicados.
- **Fuera de alcance:** devolver geometría editada en Godot. La geometría siempre se edita en Blender.

### Fase 7 — Extras (opcional, a elegir)
- **Del puente (genéricos):** remapeo de materiales (nombre de material de Blender → `res://materials/*.tres`, para usar shaders propios en vez de los PBR del glTF); luces (KHR_lights_punctual o entidades propias); exportar por partes para streaming.
- **Del juego (no van en el addon):** rooms, límites de cámara, minimapa estilo Aria of Sorrow y conexiones entre salas. Se construyen en `core/room/` y `ui/` del juego a partir de áreas y markers exportados por el puente (por ejemplo, un `area` con la escena `entities/triggers/camera_zone/camera_zone.tscn` cuyos bounds definen la sala).

## 9. Testing

- **Blender:** tests headless como en Sculpt Brush Shelf (`blender -b --factory-startup`): construir una escena de prueba por código, exportar y validar el JSON y el binario (alcance, escala horneada, conversión de ejes, uids, vertex colors en el GLB, hash de assets, manijas de curvas).
- **Godot:** script headless con Godot 4.7.2 (`/home/luis/GIT/personal/Godot4.7/Godot_v4.7.2-stable_linux.x86_64 --headless --script`) que construye un nivel desde un manifiesto de prueba y comprueba el árbol resultante (tipos de nodo, formas, props, `NodePath` resueltos, idempotencia).
- **Fixtures compartidos:** el manifiesto generado por los tests de Blender es la entrada de los tests de Godot; así el contrato se prueba de punta a punta.
- La interfaz (paneles, overlay de volúmenes y formas) queda en un checklist manual en el README, igual que en el otro addon.

## 10. Riesgos y gotchas conocidos

- `export_vertex_color='MATERIAL'` (valor por defecto del exportador glTF) descarta los colores que no usa ningún material → el exportador fija sus propios parámetros (4.4).
- Escala no uniforme o negativa en colisiones → se hornea o se rechaza en el validador (4.3).
- Uids duplicados por Ctrl+D / Shift+D en Blender → detección en el validador.
- Exportar GLB cambia la selección (ya pasó en el scatter) → guardar y restaurar la selección y el objeto activo; el staging trabaja en una escena auxiliar.
- Las instancias de Geometry Nodes solo son válidas durante la iteración de `depsgraph.object_instances` → copiar las matrices al vuelo (ya resuelto en el scatter).
- Rebuild sobre nodos editados a mano en Godot → comprobación de cambios antes de sobrescribir (fase 6).
- Reimport masivo de Godot si se reescriben GLBs idénticos → caché por hash, sin tocar archivos que no cambiaron.
- Renombrar escenas en Godot rompe las rutas `res://` del manifiesto → el builder informa de los placeholders con escena inexistente en vez de fallar en silencio; más adelante se puede usar `uid://` de Godot.

## 11. Estado

- **B0 terminada (2026-11-01):** extensión de Blender, contrato en `FORMAT.md`, `LevelBuilder` en el juego.
- **B1 y B2 implementadas (2026-11-01, addon 0.2.0):** alcance por colección raíz, uids, validación, GLB estático por staging (colores de vértice a Face Corner + Byte Color), placeholders con props y `$ref`, marcadores, colisores primitivos (caja, esfera, cápsula, cilindro, convexa, trimesh) con escala horneada y grupos de cuerpo, áreas, paths. En Godot: construcción de todo eso y extensión de importación que activa los colores de vértice en los materiales. Primera sala real: `levels/catedral/catedral_01/`. Falta el checklist manual (README del addon y `plan_de_fases.md` G2).
- Decisiones tomadas al implementar: capas como números en el manifiesto (no nombres); valores por defecto de capa por rol (colisor → `world`, área → detecta `player`); props de marcadores, paths y áreas sin escena como metadata; `Path3D` usa solo el primer spline.
- **Siguiente:** G3 (combate y primer enemigo) y después B3 (Geometry Nodes → MultiMesh).
